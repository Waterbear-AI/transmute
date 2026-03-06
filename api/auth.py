import hashlib
import hmac
import logging
import uuid
from typing import Optional

import bcrypt
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import get_settings
from db.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)

COOKIE_NAME = "transmute_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


# --- Request/Response Models ---

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    user_id: str
    name: str
    email: str
    current_phase: Optional[str] = None


# --- Cookie Signing ---

def _sign_cookie(value: str) -> str:
    secret = get_settings().cookie_secret
    signature = hmac.new(
        secret.encode(), value.encode(), hashlib.sha256
    ).hexdigest()
    return f"{value}.{signature}"


def _verify_cookie(signed_value: str) -> Optional[str]:
    if "." not in signed_value:
        return None
    value, signature = signed_value.rsplit(".", 1)
    expected = hmac.new(
        get_settings().cookie_secret.encode(), value.encode(), hashlib.sha256
    ).hexdigest()
    if hmac.compare_digest(signature, expected):
        return value
    return None


def _set_session_cookie(response: Response, user_id: str) -> None:
    signed = _sign_cookie(user_id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=signed,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def get_current_user_id(
    transmute_session: Optional[str] = Cookie(None),
) -> str:
    if not transmute_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = _verify_cookie(transmute_session)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user_id


# --- Endpoints ---

@router.post("/register", response_model=UserResponse)
@limiter.limit("5/hour")
def register(request: Request, body: RegisterRequest, response: Response):
    user_id = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(
        body.password.encode(), bcrypt.gensalt()
    ).decode()

    with get_db_session() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (body.email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, body.name, body.email, password_hash),
        )

    _set_session_cookie(response, user_id)
    return UserResponse(
        user_id=user_id, name=body.name, email=body.email, current_phase="orientation"
    )


@router.post("/login", response_model=UserResponse)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, response: Response):
    with get_db_session() as conn:
        row = conn.execute(
            "SELECT id, name, email, password_hash, current_phase FROM users WHERE email = ?",
            (body.email,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not bcrypt.checkpw(body.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    _set_session_cookie(response, row["id"])
    return UserResponse(
        user_id=row["id"],
        name=row["name"],
        email=row["email"],
        current_phase=row["current_phase"],
    )


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
def me(user_id: str = Cookie(None, alias=COOKIE_NAME)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    verified_id = _verify_cookie(user_id)
    if not verified_id:
        raise HTTPException(status_code=401, detail="Invalid session")

    with get_db_session() as conn:
        row = conn.execute(
            "SELECT id, name, email, current_phase FROM users WHERE id = ?",
            (verified_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="User not found")

    return UserResponse(
        user_id=row["id"],
        name=row["name"],
        email=row["email"],
        current_phase=row["current_phase"],
    )
