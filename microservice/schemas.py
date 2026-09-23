"""
Validation for the classifier's output, and the text-size helper used
before writing to Notion. Nothing here touches an API, so tests can import
it directly.
"""
import logging
from typing import Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from confidence import HEAVY_FLAGS, LIGHT_FLAGS

logger = logging.getLogger("inboxpilot.schemas")

Category = Literal["job_opportunity", "meeting", "deadline", "irrelevant"]
Priority = Literal["high", "medium", "low"]

CATEGORIES = get_args(Category)
PRIORITIES = get_args(Priority)
# Flags the LLM may return. "attachment_unparsed" is added later by main.py,
# not by the model, so it isn't allowed here.
FLAGS = tuple(sorted(HEAVY_FLAGS | LIGHT_FLAGS))

NOTION_TEXT_LIMIT = 2000


class ClassificationError(Exception):
    """
    Raised when the LLM's response can't be parsed as the expected JSON
    object, or when the Groq call itself fails. Callers should catch this
    and decide how to handle an email that couldn't be classified, rather
    than letting it crash the request as an unhandled 500.
    """
    def __init__(self, message: str, raw_output: str = None):
        super().__init__(message)
        self.raw_output = raw_output


# Groq strict structured outputs: every property must be in "required" and
# every object needs additionalProperties: false. Nullable fields use a type
# array. Must stay in sync with Classification below (test_schemas checks it).
_PROPERTIES = {
    "category": {"type": "string", "enum": list(CATEGORIES)},
    "source_name": {"type": "string"},
    "item_title": {"type": "string"},
    "event_date": {"type": ["string", "null"]},
    "location_or_link": {"type": ["string", "null"]},
    "priority": {"type": "string", "enum": list(PRIORITIES)},
    "summary": {"type": "string"},
    "flags": {"type": "array", "items": {"type": "string", "enum": list(FLAGS)}},
}

CLASSIFICATION_JSON_SCHEMA = {
    "type": "object",
    "properties": _PROPERTIES,
    "required": list(_PROPERTIES),
    "additionalProperties": False,
}


class Classification(BaseModel):
    """
    Safety net applied to whatever the model returns, in case strict mode
    or the model changes. Only category is required: without it the email
    can't be handled, so it fails as a ClassificationError. Every other
    field falls back to a default instead of failing.
    """
    model_config = ConfigDict(extra="ignore")

    category: Category
    source_name: str = "Unknown"
    item_title: str = "Untitled"
    event_date: Optional[str] = None
    location_or_link: Optional[str] = None
    priority: Priority = "medium"
    summary: str = ""
    flags: list[str] = Field(default_factory=list)

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value):
        # No fallback on purpose: an unknown value still fails the Literal check.
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("priority", mode="before")
    @classmethod
    def _priority_or_medium(cls, value):
        if isinstance(value, str) and value.strip().lower() in PRIORITIES:
            return value.strip().lower()
        logger.warning(f"Unknown priority {value!r}, using 'medium'")
        return "medium"

    @field_validator("source_name", "item_title", "summary", "event_date", "location_or_link", mode="before")
    @classmethod
    def _text_or_default(cls, value, info):
        # Null, blank or non-string values fall back to the field's default.
        if isinstance(value, str) and value.strip():
            return value.strip()
        return cls.model_fields[info.field_name].default

    @field_validator("flags", mode="before")
    @classmethod
    def _known_flags(cls, value):
        if not isinstance(value, list):
            if value is not None:
                logger.warning(f"flags is not a list ({value!r}), using []")
            return []

        flags = []
        for flag in value:
            if flag not in FLAGS:
                logger.warning(f"Dropping unknown flag {flag!r}")
            elif flag not in flags:
                flags.append(flag)
        return flags


def validate_classification(data, raw_output: str = None) -> dict:
    """
    Checks parsed model output and returns it as a plain dict with all
    fields present. Raises ClassificationError if it isn't a JSON object or
    the category is missing or unknown.
    """
    if not isinstance(data, dict):
        raise ClassificationError(
            f"Model output is not a JSON object (got {type(data).__name__})",
            raw_output=raw_output
        )

    try:
        return Classification.model_validate(data).model_dump()
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'output'}: {err['msg']}"
            for err in e.errors()
        )
        raise ClassificationError(f"Invalid classification: {problems}", raw_output=raw_output) from e


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def truncate_text(text: Optional[str], limit: int = NOTION_TEXT_LIMIT, suffix: str = "…") -> str:
    """
    Cuts text to at most `limit` characters, ending with `suffix` when cut.
    Length is counted in UTF-16 units (an emoji counts as 2), which is the
    safe way to measure against Notion's 2000-character limit. None becomes "".
    """
    if not text:
        return ""
    if _utf16_len(text) <= limit:
        return text

    budget = limit - _utf16_len(suffix)
    kept, used = [], 0
    for ch in text:
        size = 2 if ord(ch) > 0xFFFF else 1
        if used + size > budget:
            break
        kept.append(ch)
        used += size
    return "".join(kept) + suffix
