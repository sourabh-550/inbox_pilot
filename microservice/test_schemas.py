"""
Tests for schemas. Run from inside microservice/ with either:
    python test_schemas.py
    pytest test_schemas.py
"""
from confidence import HEAVY_FLAGS, LIGHT_FLAGS
from schemas import (
    CLASSIFICATION_JSON_SCHEMA, CATEGORIES, PRIORITIES, FLAGS, NOTION_TEXT_LIMIT,
    Classification, ClassificationError, validate_classification, truncate_text,
)

VALID = {
    "category": "meeting",
    "source_name": "TechCorp",
    "item_title": "Interview Invitation",
    "event_date": "2026-08-10T15:00:00",
    "location_or_link": "Google Meet",
    "priority": "high",
    "summary": "Interview on August 10th at 3 PM via Google Meet.",
    "flags": [],
}


def _with(**changes):
    return {**VALID, **changes}


def _utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def _assert_raises_classification_error(data):
    try:
        validate_classification(data, raw_output="raw")
    except ClassificationError as e:
        assert e.raw_output == "raw"
        return
    raise AssertionError(f"expected ClassificationError for {data!r}")


def test_valid_output_is_unchanged():
    assert validate_classification(VALID) == VALID


def test_null_priority_becomes_medium():
    assert validate_classification(_with(priority=None))["priority"] == "medium"


def test_unknown_priority_becomes_medium():
    assert validate_classification(_with(priority="urgent"))["priority"] == "medium"


def test_priority_is_normalized():
    assert validate_classification(_with(priority=" High "))["priority"] == "high"


def test_unknown_category_raises():
    _assert_raises_classification_error(_with(category="newsletter"))


def test_null_category_raises():
    _assert_raises_classification_error(_with(category=None))


def test_missing_category_raises():
    data = dict(VALID)
    del data["category"]
    _assert_raises_classification_error(data)


def test_empty_object_raises():
    _assert_raises_classification_error({})


def test_category_is_normalized():
    assert validate_classification(_with(category="Meeting"))["category"] == "meeting"


def test_unknown_flag_is_dropped():
    result = validate_classification(_with(flags=["missing_date", "made_up_flag", "vague_subject"]))
    assert result["flags"] == ["missing_date", "vague_subject"]


def test_duplicate_flags_are_removed():
    result = validate_classification(_with(flags=["vague_subject", "vague_subject"]))
    assert result["flags"] == ["vague_subject"]


def test_non_list_flags_become_empty():
    assert validate_classification(_with(flags="missing_date"))["flags"] == []


def test_missing_fields_get_defaults():
    assert validate_classification({"category": "deadline"}) == {
        "category": "deadline",
        "source_name": "Unknown",
        "item_title": "Untitled",
        "event_date": None,
        "location_or_link": None,
        "priority": "medium",
        "summary": "",
        "flags": [],
    }


def test_null_and_blank_strings_get_defaults():
    result = validate_classification(_with(
        source_name=None, item_title="   ", summary=None, event_date="", location_or_link=42
    ))
    assert result["source_name"] == "Unknown"
    assert result["item_title"] == "Untitled"
    assert result["summary"] == ""
    assert result["event_date"] is None
    assert result["location_or_link"] is None


def test_extra_keys_are_dropped():
    assert "confidence" not in validate_classification(_with(confidence=0.9))


def test_non_object_output_raises():
    for data in ([VALID], "meeting", 42, None):
        _assert_raises_classification_error(data)


def test_schema_properties_match_model_fields():
    fields = set(Classification.model_fields)
    assert set(CLASSIFICATION_JSON_SCHEMA["properties"]) == fields
    assert set(CLASSIFICATION_JSON_SCHEMA["required"]) == fields
    assert CLASSIFICATION_JSON_SCHEMA["additionalProperties"] is False


def test_schema_enums_match_allowed_values():
    props = CLASSIFICATION_JSON_SCHEMA["properties"]
    assert set(props["category"]["enum"]) == set(CATEGORIES)
    assert set(props["priority"]["enum"]) == set(PRIORITIES)
    assert set(props["flags"]["items"]["enum"]) == set(FLAGS)


def test_flags_match_confidence_rubric():
    assert set(FLAGS) == HEAVY_FLAGS | LIGHT_FLAGS


def test_truncate_short_text_is_unchanged():
    assert truncate_text("hello") == "hello"
    assert truncate_text("x" * NOTION_TEXT_LIMIT) == "x" * NOTION_TEXT_LIMIT


def test_truncate_none_and_empty_become_empty_string():
    assert truncate_text(None) == ""
    assert truncate_text("") == ""


def test_truncate_long_text():
    result = truncate_text("x" * 5000)
    assert len(result) == NOTION_TEXT_LIMIT
    assert result.endswith("…")


def test_truncate_counts_emoji_as_two():
    result = truncate_text("😀" * 1500)
    assert _utf16_len(result) <= NOTION_TEXT_LIMIT
    assert result.endswith("…")
    assert set(result[:-1]) == {"😀"}


def test_truncate_custom_limit_and_suffix():
    assert truncate_text("abcdefghij", limit=8, suffix="[cut]") == "abc[cut]"


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"ok   {name}")
    print(f"all {len(tests)} tests passed")
