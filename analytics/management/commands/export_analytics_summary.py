import sys
from argparse import ArgumentParser
from typing import Any

from typing_extensions import override

from analytics.lib.analytics_summary import (
    AnalyticsSummaryError,
    compute_growth_rate,
    export_summary_as_csv,
    export_summary_as_json,
    generate_full_report,
    get_fill_state_summary,
    get_time_series_data,
)
from analytics.lib.counts import COUNT_STATS
from zerver.lib.management import ZulipBaseCommand
from zerver.models import Realm


class Command(ZulipBaseCommand):
    help = """Export analytics summary data.

    Generates summary reports of analytics statistics, including
    aggregate values, growth rates, and fill state information.
    Supports JSON and CSV output formats."""

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--format",
            "-f",
            choices=["json", "csv"],
            default="json",
            help="Output format (default: json).",
        )
        parser.add_argument(
            "--days",
            "-d",
            type=int,
            default=30,
            help="Number of days to include in the summary (default: 30).",
        )
        parser.add_argument(
            "--stat",
            "-s",
            action="append",
            dest="stats",
            help="Specific stat property to include. Can be repeated. If omitted, all stats are included.",
        )
        parser.add_argument(
            "--realm",
            "-r",
            help="Realm string_id to filter by. If omitted, installation-wide data is used.",
        )
        parser.add_argument(
            "--fill-state",
            action="store_true",
            help="Only output the fill state summary for all stats.",
        )
        parser.add_argument(
            "--growth",
            action="store_true",
            help="Include growth rate analysis in the output.",
        )
        parser.add_argument(
            "--time-series",
            action="store_true",
            help="Include time series data in the output.",
        )
        parser.add_argument(
            "--output",
            "-o",
            help="Write output to a file instead of stdout.",
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        try:
            self.run_export(options)
        except AnalyticsSummaryError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            sys.exit(1)

    def run_export(self, options: dict[str, Any]) -> None:
        realm = None
        if options["realm"] is not None:
            try:
                realm = Realm.objects.get(string_id=options["realm"])
            except Realm.DoesNotExist:
                raise AnalyticsSummaryError(f'Realm "{options["realm"]}" does not exist.')

        stat_properties = options["stats"]
        if stat_properties is not None:
            for prop in stat_properties:
                if prop not in COUNT_STATS:
                    raise AnalyticsSummaryError(f'Unknown stat property: "{prop}"')

        days = options["days"]
        if days <= 0:
            raise AnalyticsSummaryError("--days must be a positive integer.")

        if options["fill_state"]:
            result = get_fill_state_summary()
        elif options["time_series"] and stat_properties is not None:
            result = self._get_time_series_output(realm, days, stat_properties)
        elif options["growth"] and stat_properties is not None:
            result = self._get_growth_output(realm, days, stat_properties)
        else:
            result = generate_full_report(
                realm=realm,
                days=days,
                stat_properties=stat_properties,
            )

        output_format = options["format"]
        if output_format == "json":
            output = export_summary_as_json(result)
        else:
            output = export_summary_as_csv(result)

        if options["output"] is not None:
            with open(options["output"], "w") as f:
                f.write(output)
            self.stdout.write(self.style.SUCCESS(f"Summary written to {options['output']}"))
        else:
            self.stdout.write(output)

    def _get_time_series_output(
        self,
        realm: Realm | None,
        days: int,
        stat_properties: list[str],
    ) -> dict[str, Any]:
        from django.utils.timezone import now as timezone_now
        from datetime import timedelta

        now = timezone_now()
        start_time = now - timedelta(days=days)
        result: dict[str, Any] = {"generated_at": now.isoformat(), "period_days": days}

        for prop in stat_properties:
            stat = COUNT_STATS[prop]
            last_fill = stat.last_successful_fill()
            effective_end = last_fill if last_fill is not None else now
            result[prop] = get_time_series_data(stat, start_time, effective_end, realm)

        return result

    def _get_growth_output(
        self,
        realm: Realm | None,
        days: int,
        stat_properties: list[str],
    ) -> dict[str, Any]:
        from django.utils.timezone import now as timezone_now
        from datetime import timedelta

        now = timezone_now()
        start_time = now - timedelta(days=days)
        result: dict[str, Any] = {"generated_at": now.isoformat(), "period_days": days}

        for prop in stat_properties:
            stat = COUNT_STATS[prop]
            last_fill = stat.last_successful_fill()
            effective_end = last_fill if last_fill is not None else now
            result[prop] = compute_growth_rate(stat, start_time, effective_end, realm)

        return result
