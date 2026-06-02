from typing import Any

from typing_extensions import override

from analytics.lib.check_analytics_state import AnalyticsStateChecker
from scripts.lib.zulip_tools import atomic_nagios_write
from zerver.lib.management import ZulipBaseCommand


class Command(ZulipBaseCommand):
    help = """Checks FillState table.

    Run as a cron job that runs every hour."""

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        fill_state = AnalyticsStateChecker.get_fill_state()
        atomic_nagios_write("check-analytics-state", fill_state.status, fill_state.message)
