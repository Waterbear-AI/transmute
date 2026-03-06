import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _get_db_path() -> str:
    return get_settings().db_path


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db_session():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, "
        "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()


def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {row[0] for row in rows}


def _get_migration_files() -> list[tuple[int, Path]]:
    if not MIGRATIONS_DIR.exists():
        return []
    files = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            version = int(f.stem.split("_")[0])
            files.append((version, f))
        except (ValueError, IndexError):
            logger.warning("Skipping non-numbered migration file: %s", f.name)
    return files


def run_migrations(db_path: str | None = None) -> int:
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        _ensure_schema_version_table(conn)
        applied = _get_applied_versions(conn)
        migrations = _get_migration_files()
        applied_count = 0

        for version, filepath in migrations:
            if version in applied:
                continue

            logger.info("Applying migration %03d: %s", version, filepath.name)
            sql = filepath.read_text()
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
            conn.commit()
            applied_count += 1

        if applied_count:
            logger.info("Applied %d migration(s)", applied_count)
        else:
            logger.info("Database is up to date")

        return applied_count
    finally:
        conn.close()
