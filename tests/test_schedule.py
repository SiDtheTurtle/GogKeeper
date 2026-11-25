"""Schedule settings: valid cron expressions are accepted, and ones that would run on the wrong days are refused."""
import pytest

from gogkeeper import schedule

VARIABLE = "GOGKEEPER_SYNC_SCHEDULE"


@pytest.mark.parametrize("expression", [
    "0 0 * * *",
    "30 2 * * sun",
    "0 3 * * mon-fri",
    "0 */6 * * *",
    "15 4 1 * *",
    "  0 0 * * *  ",
])
def test_valid_schedules_are_accepted(monkeypatch, expression):
    monkeypatch.setenv(VARIABLE, expression)
    assert schedule.read_schedule(VARIABLE) is not None


@pytest.mark.parametrize("expression, reason", [
    ("0 0 * * 0", "use names for the day of week"),       # APScheduler 3 reads 0 as Monday, cron as Sunday
    ("0 0 * * 1-5", "use names for the day of week"),
    ("0 0 1 * mon", "not both"),                           # cron means either; APScheduler would mean both
    ("0 0 * *", "Wrong number of fields"),
    ("0 0 * * * *", "Wrong number of fields"),
    ("61 0 * * *", ""),                                    # out of range
])
def test_invalid_schedules_are_refused_naming_the_setting(monkeypatch, expression, reason):
    monkeypatch.setenv(VARIABLE, expression)
    with pytest.raises(RuntimeError, match=f"{VARIABLE} is invalid: .*{reason}"):
        schedule.read_schedule(VARIABLE)


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_schedule_is_refused_naming_the_setting(monkeypatch, value):
    monkeypatch.setenv(VARIABLE, value)
    with pytest.raises(RuntimeError, match=f"{VARIABLE} isn't set"):
        schedule.read_schedule(VARIABLE)


def test_a_missing_schedule_is_refused_naming_the_setting(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    with pytest.raises(RuntimeError, match=f"{VARIABLE} isn't set"):
        schedule.read_schedule(VARIABLE)
