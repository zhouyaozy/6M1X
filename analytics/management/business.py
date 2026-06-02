from datetime import datetime, timezone

from django.core.management.base import CommandError
from django.utils.dateparse import parse_datetime

from analytics.lib.counts import ALL_COUNT_STATS, CountStat
from zerver.lib.timestamp import floor_to_hour


def get_count_stat(stat_property: str) -> CountStat:
    try:
        return ALL_COUNT_STATS[stat_property]
    except KeyError as exc:
        raise CommandError(f"Invalid property: {stat_property}") from exc


def get_count_stats_to_process(stat_property: str | None) -> list[CountStat]:
    if stat_property is None:
        return list(ALL_COUNT_STATS.values())
    return [get_count_stat(stat_property)]


def resolve_fill_to_time(time_string: str, *, use_utc: bool) -> datetime:
    fill_to_time = parse_datetime(time_string)
    if fill_to_time is None:
        raise CommandError(f"Invalid time: {time_string}")
    if use_utc:
        fill_to_time = fill_to_time.replace(tzinfo=timezone.utc)
    if fill_to_time.tzinfo is None:
        raise CommandError(
            "--time must be time-zone-aware. Maybe you meant to use the --utc option?"
        )
    return floor_to_hour(fill_to_time.astimezone(timezone.utc))
