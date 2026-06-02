import hashlib
import time
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from analytics.lib.counts import logger, process_count_stat
from analytics.management import get_count_stats_to_process, resolve_fill_to_time
from zerver.lib.management import ZulipBaseCommand, abort_cron_during_deploy, abort_unless_locked
from zerver.lib.remote_server import send_server_data_to_push_bouncer, should_send_analytics_data
from zerver.models import Realm


class Command(ZulipBaseCommand):
    help = """Fills Analytics tables.

    Run as a cron job that runs every hour."""

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--time",
            "-t",
            help="Update stat tables from current state to --time. Defaults to the current time.",
            default=timezone_now().isoformat(),
        )
        parser.add_argument("--utc", action="store_true", help="Interpret --time in UTC.")
        parser.add_argument(
            "--stat", "-s", help="CountStat to process. If omitted, all stats are processed."
        )
        parser.add_argument(
            "--verbose", action="store_true", help="Print timing information to stdout."
        )

    @override
    @abort_cron_during_deploy
    @abort_unless_locked
    def handle(self, *args: Any, **options: Any) -> None:
        self.run_update_analytics_counts(options)

    def run_update_analytics_counts(self, options: dict[str, Any]) -> None:
        if not Realm.objects.exists():
            logger.info("No realms, stopping update_analytics_counts")
            return

        fill_to_time = resolve_fill_to_time(options["time"], use_utc=options["utc"])
        stats = get_count_stats_to_process(options["stat"])

        logger.info("Starting updating analytics counts through %s", fill_to_time)
        if options["verbose"]:
            start = time.time()
            last = start

        for stat in stats:
            process_count_stat(stat, fill_to_time)
            if options["verbose"]:
                print(f"Updated {stat.property} in {time.time() - last:.3f}s")
                last = time.time()

        if options["verbose"]:
            print(
                f"Finished updating analytics counts through {fill_to_time} in {time.time() - start:.3f}s"
            )
        logger.info("Finished updating analytics counts through %s", fill_to_time)

        if should_send_analytics_data():
            assert settings.ZULIP_ORG_ID
            delay = int.from_bytes(
                hashlib.sha256(settings.ZULIP_ORG_ID.encode()).digest(), byteorder="big"
            ) % (60 * 10)
            logger.info("Sleeping %d seconds before reporting...", delay)
            time.sleep(delay)

            send_server_data_to_push_bouncer(consider_usage_statistics=True, raise_on_error=True)
