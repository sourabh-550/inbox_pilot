"""
Tests for timezone_utils. Run from inside microservice/ with either:
    python test_timezone_utils.py
    pytest test_timezone_utils.py
"""
from timezone_utils import to_ist, is_date_only


def test_none_returns_none():
    assert to_ist(None) is None


def test_empty_string_returns_none():
    assert to_ist("") is None


def test_date_only_is_unchanged():
    assert to_ist("2026-08-10") == "2026-08-10"


def test_naive_datetime_gets_ist_offset():
    assert to_ist("2026-08-10T15:00:00") == "2026-08-10T15:00:00+05:30"


def test_ist_datetime_is_unchanged():
    assert to_ist("2026-08-10T15:00:00+05:30") == "2026-08-10T15:00:00+05:30"


def test_negative_offset_is_converted_to_ist():
    assert to_ist("2026-08-10T10:00:00-04:00") == "2026-08-10T19:30:00+05:30"


def test_utc_z_is_converted_to_ist():
    assert to_ist("2026-08-10T09:30:00Z") == "2026-08-10T15:00:00+05:30"


def test_unparseable_raises_value_error():
    try:
        to_ist("next Friday")
    except ValueError:
        return
    raise AssertionError("expected ValueError for 'next Friday'")


def test_is_date_only():
    assert is_date_only("2026-08-10")
    assert not is_date_only("2026-08-10T15:00:00+05:30")
    assert not is_date_only("")
    assert not is_date_only(None)


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"ok   {name}")
    print(f"all {len(tests)} tests passed")
