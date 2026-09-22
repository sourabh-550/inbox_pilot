import os
import uuid
import hashlib
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("inboxpilot.dedup")

# How long a 'processing' claim is honoured before another request may take
# it over (e.g. the original request crashed without releasing its claim).
STALE_CLAIM_MINUTES = 10

# Run at the start of every transaction: connect_timeout only covers opening
# the connection, so without this a query that stalls after connecting could
# still hang the request.
STATEMENT_TIMEOUT_SQL = "SET LOCAL statement_timeout = '5s'"

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


def claim(email_id: str) -> str | None:
    """
    Atomically claims an email for processing. Returns a claim token if this
    request now owns the email, or None if it is already done or another
    request is actively processing it (i.e. skip as a duplicate).

    A single INSERT ... ON CONFLICT statement does the check and the write,
    so two concurrent requests for the same email can't both win. An
    existing 'processing' row older than STALE_CLAIM_MINUTES is taken over
    with a fresh token, which makes the old owner's mark_done/release no-ops.
    """
    token = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            conn.execute(text(STATEMENT_TIMEOUT_SQL))
            row = conn.execute(
                text(
                    "INSERT INTO inboxpilot_v1_dedup (email_id, status, claimed_at, claim_token) "
                    "VALUES (:email_id, 'processing', now(), :token) "
                    "ON CONFLICT (email_id) DO UPDATE "
                    "SET claimed_at = now(), claim_token = EXCLUDED.claim_token "
                    "WHERE inboxpilot_v1_dedup.status = 'processing' "
                    "AND inboxpilot_v1_dedup.claimed_at < now() - make_interval(mins => :stale_minutes) "
                    "RETURNING claim_token"
                ),
                {"email_id": email_id, "token": token, "stale_minutes": STALE_CLAIM_MINUTES}
            ).fetchone()
    except (OperationalError, SQLAlchemyError) as e:
        logger.error(f"Dedup claim failed for {email_id}: {e}")
        raise DedupUnavailableError(f"Could not claim email: {e}")

    return token if row is not None else None


def mark_done(email_id: str, token: str):
    """
    Marks a claimed email as fully processed. Only succeeds if this request
    still owns the claim; otherwise logs a warning and does nothing.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(STATEMENT_TIMEOUT_SQL))
            result = conn.execute(
                text(
                    "UPDATE inboxpilot_v1_dedup "
                    "SET status = 'done', processed_at = now() "
                    "WHERE email_id = :email_id AND claim_token = :token "
                    "AND status = 'processing'"
                ),
                {"email_id": email_id, "token": token}
            )
            rowcount = result.rowcount
    except (OperationalError, SQLAlchemyError) as e:
        logger.error(f"Failed to mark {email_id} as done: {e}")
        raise DedupUnavailableError(f"Could not mark email as done: {e}")

    if rowcount == 0:
        logger.warning(f"mark_done: lost ownership of {email_id} (claim taken over or removed)")


def release(email_id: str, token: str):
    """
    Drops this request's claim so the email can be retried later. Only
    deletes a row that this request still owns and that is still
    'processing', so it can never undo a finished email.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(STATEMENT_TIMEOUT_SQL))
            result = conn.execute(
                text(
                    "DELETE FROM inboxpilot_v1_dedup "
                    "WHERE email_id = :email_id AND claim_token = :token "
                    "AND status = 'processing'"
                ),
                {"email_id": email_id, "token": token}
            )
            rowcount = result.rowcount
    except (OperationalError, SQLAlchemyError) as e:
        logger.error(f"Failed to release claim on {email_id}: {e}")
        raise DedupUnavailableError(f"Could not release claim: {e}")

    if rowcount == 0:
        logger.warning(f"release: lost ownership of {email_id} (claim taken over or removed)")
