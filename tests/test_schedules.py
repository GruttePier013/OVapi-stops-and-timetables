from custom_components.ovapi_bustijden.schedules import DEFAULT_SCHEDULE, SCHEDULES, fires_at


def times(key):
    return [(h, m) for h in range(24) for m in range(60) if fires_at(key, h, m)]


def test_never_and_daily_and_hourly():
    assert times("never") == []
    assert times("daily") == [(6, 0)]
    assert len(times("hourly")) == 24 and all(m == 0 for _, m in times("hourly"))


def test_windows_include_end_point():
    morning = times("every_30_min_06_09")
    assert morning[0] == (6, 0) and morning[-1] == (9, 0) and (9, 30) not in morning
    assert len(morning) == 7  # 06:00..09:00 elk half uur
    evening = times("every_30_min_16_19")
    assert evening[0] == (16, 0) and evening[-1] == (19, 0)


def test_every_5_and_rush():
    assert len(times("every_5_min")) == 24 * 12
    rush = times("every_5_min_rush")
    assert (6, 0) in rush and (9, 0) in rush and (9, 5) not in rush and (12, 0) not in rush and (18, 55) in rush and (19, 0) in rush
    assert set(SCHEDULES) >= {"never", "daily", "hourly", "every_30_min_06_09", "every_30_min_16_19"}


def test_default_is_never():
    assert DEFAULT_SCHEDULE == "never" and "every_minute" not in SCHEDULES
