from typing import Any

from analytics.lib.counts import check_analytics_fill_state
from scripts.lib.zulip_tools import atomic_nagios_write
from zerver.lib.management import ZulipBaseCommand


class Command(ZulipBaseCommand):
    help = """Checks FillState table.

    Run as a cron job that runs every hour."""

    def handle(self, *args: Any, **options: Any) -> None:
        fill_state = check_analytics_fill_state()
        atomic_nagios_write("check-analytics-state", fill_state.status, fill_state.message)
