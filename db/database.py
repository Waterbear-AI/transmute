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


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL line comments (-- ...) from a SQL string.

    Line comments must be stripped before splitting on ';' to avoid
    treating an entire comment-prefixed block (e.g. '-- note\\nCREATE TABLE')
    as a comment-only statement.
    """
    lines = []
    for line in sql.splitlines():
        # Remove inline and full-line comments, preserving the line so that
        # surrounding whitespace and newlines keep statements separated.
        stripped = line.split("--")[0]
        lines.append(stripped)
    return "\n".join(lines)


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
            raw_sql = filepath.read_text()

            # Strip line comments BEFORE splitting so that comment-preceded
            # statements (e.g. "-- note\nCREATE TABLE") are not skipped.
            sql = _strip_sql_comments(raw_sql)

            # Split into individual statements and execute each one.
            # executescript() auto-commits and can silently skip failed
            # statements, so we run them individually within a transaction.
            statements = [s.strip() for s in sql.split(";") if s.strip()]

            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
                conn.commit()
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(
                    "Migration %03d failed: %s",
                    version, e,
                )
                raise

            applied_count += 1

        if applied_count:
            logger.info("Applied %d migration(s)", applied_count)
        else:
            logger.info("Database is up to date")

        return applied_count
    finally:
        conn.close()
