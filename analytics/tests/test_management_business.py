from datetime import datetime, timezone

from django.core.management.base import CommandError

from analytics.lib.counts import ALL_COUNT_STATS
from analytics.management import get_count_stat, get_count_stats_to_process, resolve_fill_to_time
from zerver.lib.test_classes import ZulipTestCase


class TestAnalyticsManagementBusiness(ZulipTestCase):
    def test_get_count_stat(self) -> None:
        stat = get_count_stat("messages_sent:is_bot:hour")
        self.assertEqual(stat, ALL_COUNT_STATS["messages_sent:is_bot:hour"])

    def test_get_count_stat_rejects_unknown_property(self) -> None:
        with self.assertRaisesRegex(CommandError, "Invalid property: invalid-property"):
            get_count_stat("invalid-property")

    def test_get_count_stats_to_process_with_none(self) -> None:
        stats = get_count_stats_to_process(None)
        self.assertEqual(stats, list(ALL_COUNT_STATS.values()))

    def test_resolve_fill_to_time_normalizes_to_utc_hour_boundary(self) -> None:
        fill_to_time = resolve_fill_to_time("2026-06-02T15:45:12+08:00", use_utc=False)
        self.assertEqual(fill_to_time, datetime(2026, 6, 2, 7, 0, tzinfo=timezone.utc))

    def test_resolve_fill_to_time_adds_utc_when_requested(self) -> None:
        fill_to_time = resolve_fill_to_time("2026-06-02T15:45:12", use_utc=True)
        self.assertEqual(fill_to_time, datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc))

    def test_resolve_fill_to_time_rejects_naive_datetime_without_utc_flag(self) -> None:
        with self.assertRaisesRegex(CommandError, "--time must be time-zone-aware"):
            resolve_fill_to_time("2026-06-02T15:45:12", use_utc=False)

    def test_resolve_fill_to_time_rejects_invalid_datetime(self) -> None:
        with self.assertRaisesRegex(CommandError, "Invalid time: invalid-time"):
            resolve_fill_to_time("invalid-time", use_utc=False)
