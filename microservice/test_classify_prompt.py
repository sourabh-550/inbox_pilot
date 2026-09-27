"""
Tests for the "current time" put into the classifier prompt. Run from inside
microservice/ with either:
    python test_classify_prompt.py
    pytest test_classify_prompt.py

No network calls: classify builds its Groq client at import time, which only
needs GROQ_API_KEY to be set, so a dummy key is used if none is present, and
the classify_email tests swap the client for a stub.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")

import classify
from classify import build_system_prompt, classify_email, SYSTEM_PROMPT_TEMPLATE, LOCAL_TZ

CANNED_OUTPUT = {
    "category": "meeting",
    "source_name": "TechCorp",
    "item_title": "Interview",
    "event_date": "2026-08-11T15:00:00",
    "location_or_link": "Google Meet",
    "priority": "high",
    "summary": "Interview tomorrow at 3 PM.",
    "flags": [],
}


def _stub_client(calls):
    """A fake Groq client that records each call and returns CANNED_OUTPUT."""
    def create(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(CANNED_OUTPUT))
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_fixed_ist_datetime_in_prompt():
    prompt = build_system_prompt(datetime(2026, 8, 10, 15, 0, tzinfo=LOCAL_TZ))
    assert "The current date and time is: 2026-08-10 15:00:00 (Monday)" in prompt


def test_utc_datetime_converted_to_ist():
    # 20:00 UTC is 01:30 IST the next day, so the date and weekday shift too.
    prompt = build_system_prompt(datetime(2026, 8, 10, 20, 0, tzinfo=timezone.utc))
    assert "The current date and time is: 2026-08-11 01:30:00 (Tuesday)" in prompt
    assert "2026-08-10 20:00:00" not in prompt


def test_other_offset_converted_to_ist():
    minus_four = timezone(timedelta(hours=-4))
    prompt = build_system_prompt(datetime(2026, 8, 10, 10, 0, tzinfo=minus_four))
    assert "The current date and time is: 2026-08-10 19:30:00 (Monday)" in prompt


def test_naive_datetime_raises_value_error():
    try:
        build_system_prompt(datetime(2026, 8, 10, 15, 0))
    except ValueError:
        return
    raise AssertionError("expected ValueError for a naive datetime")


def test_non_datetime_raises_type_error():
    for value in ["2026-08-10T15:00:00+05:30", date(2026, 8, 10)]:
        try:
            build_system_prompt(value)
        except TypeError:
            continue
        raise AssertionError(f"expected TypeError for {value!r}")


def test_none_uses_today_ist():
    # Read the date on both sides of the call so a run at midnight can't fail.
    before = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    prompt = build_system_prompt()
    after = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    assert (f"The current date and time is: {before} " in prompt
            or f"The current date and time is: {after} " in prompt)


def test_prompt_wording_unchanged():
    prompt = build_system_prompt(datetime(2026, 8, 10, 15, 0, tzinfo=LOCAL_TZ))
    expected = SYSTEM_PROMPT_TEMPLATE.format(
        current_datetime="2026-08-10 15:00:00",
        current_day_name="Monday",
    )
    assert prompt == expected


def test_classify_email_naive_now_fails_before_api_call():
    calls = []
    real_client = classify.client
    classify.client = _stub_client(calls)
    try:
        try:
            classify_email("Interview", "Tomorrow at 3 PM.", "hr@techcorp.com",
                           now=datetime(2026, 8, 10, 15, 0))
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for a naive now")
    finally:
        classify.client = real_client
    assert calls == [], "the Groq client must not be called when now is naive"


def test_classify_email_passes_now_into_prompt():
    calls = []
    real_client = classify.client
    classify.client = _stub_client(calls)
    try:
        result = classify_email("Interview", "Tomorrow at 3 PM.", "hr@techcorp.com",
                                now=datetime(2026, 8, 10, 15, 0, tzinfo=LOCAL_TZ))
    finally:
        classify.client = real_client

    assert len(calls) == 1
    system_message = calls[0]["messages"][0]
    assert system_message["role"] == "system"
    assert "The current date and time is: 2026-08-10 15:00:00 (Monday)" in system_message["content"]
    assert result["event_date"] == "2026-08-11T15:00:00"


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"ok   {name}")
    print(f"all {len(tests)} tests passed")
