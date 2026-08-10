import os
import hashlib
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("inboxpilot.dedup")

DATABASE_URL = os.environ.get("DATABASE_URL")

# connect_timeout: how long to wait when first establishing a connection to
# Supabase before giving up, instead of hanging indefinitely on a network
# blip. pool_pre_ping: checks a pooled connection is still alive before
# reusing it, so a connection that went stale doesn't surface as a
# confusing failure mid-query.
engine = create_engine(
    DATABASE_URL,
    connect_args={"connect_timeout": 5},
    pool_pre_ping=True,
)


class DedupUnavailableError(Exception):
    """
    Raised when the dedup database can't be reached or the query/insert
    fails for any reason. Callers should catch this explicitly and decide
    how to degrade (e.g. proceed without dedup protection and alert,
    rather than letting the whole request hang or 500 unhandled).
    """
    pass


def compute_fingerprint(subject: str, sender: str, body: str) -> str:
    """
    Creates a stable hash from email content, used when no real
    Gmail message ID is available (e.g. manual testing).
    """
    raw = f"{subject}|{sender}|{body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_duplicate(email_id: str) -> bool:
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT 1 FROM inboxpilot_v1_dedup WHERE email_id = :email_id"),
                {"email_id": email_id}
            )
            return result.fetchone() is not None
    except (OperationalError, SQLAlchemyError) as e:
        logger.error(f"Dedup check failed for {email_id}: {e}")
        raise DedupUnavailableError(f"Could not check for duplicate: {e}")


def mark_processed(email_id: str):
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO inboxpilot_v1_dedup (email_id) VALUES (:email_id) "
                    "ON CONFLICT (email_id) DO NOTHING"
                ),
                {"email_id": email_id}
            )
            conn.commit()
    except (OperationalError, SQLAlchemyError) as e:
        logger.error(f"Failed to mark {email_id} as processed: {e}")
        raise DedupUnavailableError(f"Could not mark email as processed: {e}")