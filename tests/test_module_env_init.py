from datetime import datetime

from modules.module_env_init import env_bool, env_bool_first, env_first, env_int, env_int_first, utc_timestamp


def test_utc_timestamp_is_timezone_aware_utc():
    parsed = datetime.fromisoformat(utc_timestamp())

    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_env_bool_parses_common_false_values(monkeypatch):
    for value in ("0", "false", "False", " no ", "off"):
        monkeypatch.setenv("FEATURE_FLAG", value)
        assert env_bool("FEATURE_FLAG", default=True) is False

    monkeypatch.setenv("FEATURE_FLAG", "yes")
    assert env_bool("FEATURE_FLAG") is True
    monkeypatch.delenv("FEATURE_FLAG")
    assert env_bool("FEATURE_FLAG", default=True) is True


def test_env_int_falls_back_for_missing_or_invalid_values(monkeypatch):
    monkeypatch.delenv("COUNT", raising=False)
    assert env_int("COUNT", 7) == 7

    monkeypatch.setenv("COUNT", "12")
    assert env_int("COUNT", 7) == 12

    monkeypatch.setenv("COUNT", "invalid")
    assert env_int("COUNT", 7) == 7


def test_env_first_helpers_ignore_empty_values(monkeypatch):
    monkeypatch.setenv("PRIMARY", "")
    monkeypatch.setenv("SECONDARY", "42")

    assert env_first(["PRIMARY", "SECONDARY"], default="fallback") == "42"
    assert env_int_first(["PRIMARY", "SECONDARY"], default=5) == 42
    assert env_bool_first(["PRIMARY", "SECONDARY"], default=False) is True

    monkeypatch.setenv("SECONDARY", "bad")
    assert env_int_first(["PRIMARY", "SECONDARY"], default=5) == 5
