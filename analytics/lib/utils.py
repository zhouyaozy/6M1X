from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Sequence

from django.utils.timezone import now as timezone_now

from analytics.lib.counts import ALL_COUNT_STATS, CountStat
from analytics.models import FillState, InstallationCount, RealmCount, StreamCount, UserCount
from zerver.lib.timestamp import TimeZoneNotUTCError, verify_UTC
from zerver.models import Realm


@dataclass
class StatSummary:
    property: str
    frequency: str
    last_fill: datetime | None
    total_records: int
    latest_value: int | None


@dataclass
class AnalyticsHealthReport:
    status: Literal["healthy", "warning", "critical", "unknown"]
    summary: str
    details: list[str]
    stat_summaries: list[StatSummary]


def get_stat_summary(stat: CountStat) -> StatSummary:
    last_fill = stat.last_successful_fill()
    total_records = 0
    latest_value = None

    output_table = stat.data_collector.output_table
    if output_table == UserCount:
        total_records = UserCount.objects.filter(property=stat.property).count()
        latest = UserCount.objects.filter(property=stat.property).order_by("-end_time").first()
        if latest:
            latest_value = latest.value
    elif output_table == StreamCount:
        total_records = StreamCount.objects.filter(property=stat.property).count()
        latest = StreamCount.objects.filter(property=stat.property).order_by("-end_time").first()
        if latest:
            latest_value = latest.value
    elif output_table == RealmCount:
        total_records = RealmCount.objects.filter(property=stat.property).count()
        latest = RealmCount.objects.filter(property=stat.property).order_by("-end_time").first()
        if latest:
            latest_value = latest.value
    elif output_table == InstallationCount:
        total_records = InstallationCount.objects.filter(property=stat.property).count()
        latest = InstallationCount.objects.filter(property=stat.property).order_by("-end_time").first()
        if latest:
            latest_value = latest.value

    return StatSummary(
        property=stat.property,
        frequency=stat.frequency,
        last_fill=last_fill,
        total_records=total_records,
        latest_value=latest_value,
    )


def get_analytics_health_report() -> AnalyticsHealthReport:
    if not Realm.objects.exists():
        return AnalyticsHealthReport(
            status="healthy",
            summary="No realms exist, analytics health check skipped.",
            details=[],
            stat_summaries=[],
        )

    now = timezone_now()
    warnings: list[str] = []
    criticals: list[str] = []
    stat_summaries: list[StatSummary] = []

    for property, stat in ALL_COUNT_STATS.items():
        summary = get_stat_summary(stat)
        stat_summaries.append(summary)

        last_fill = summary.last_fill
        if last_fill is None:
            criticals.append(f"{property}: Never filled")
            continue

        try:
            verify_UTC(last_fill)
        except TimeZoneNotUTCError:
            criticals.append(f"{property}: Last fill time not in UTC")
            continue

        if stat.frequency == CountStat.DAY:
            warning_threshold = timedelta(hours=26)
            critical_threshold = timedelta(hours=50)
        else:
            warning_threshold = timedelta(minutes=90)
            critical_threshold = timedelta(minutes=150)

        time_since_last_fill = now - last_fill
        if time_since_last_fill > critical_threshold:
            criticals.append(
                f"{property}: Last fill {time_since_last_fill.total_seconds() / 3600:.1f} hours ago"
            )
        elif time_since_last_fill > warning_threshold:
            warnings.append(
                f"{property}: Last fill {time_since_last_fill.total_seconds() / 3600:.1f} hours ago"
            )

    if criticals:
        status = "critical"
        summary = f"Critical issues found with {len(criticals)} stats"
    elif warnings:
        status = "warning"
        summary = f"Warnings found with {len(warnings)} stats"
    else:
        status = "healthy"
        summary = "All stats appear healthy"

    return AnalyticsHealthReport(
        status=status,
        summary=summary,
        details=warnings + criticals,
        stat_summaries=stat_summaries,
    )


def get_top_stats_by_value(
    property: str,
    limit: int = 10,
    realm: Realm | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    stat = ALL_COUNT_STATS.get(property)
    if not stat:
        return results

    output_table = stat.data_collector.output_table
    query = output_table.objects.filter(property=property)

    if realm is not None:
        if hasattr(output_table, "realm_id"):
            query = query.filter(realm_id=realm.id)

    query = query.order_by("-value")[:limit]

    for item in query:
        result = {
            "value": item.value,
            "end_time": item.end_time,
        }
        if hasattr(item, "user_id"):
            result["user_id"] = item.user_id
        if hasattr(item, "stream_id"):
            result["stream_id"] = item.stream_id
        if hasattr(item, "realm_id"):
            result["realm_id"] = item.realm_id
        if hasattr(item, "subgroup"):
            result["subgroup"] = item.subgroup
        results.append(result)

    return results


def calculate_trend(
    property: str,
    periods: int = 7,
    realm: Realm | None = None,
) -> dict[str, Any]:
    stat = ALL_COUNT_STATS.get(property)
    if not stat:
        return {"error": "Stat not found"}

    output_table = stat.data_collector.output_table
    now = timezone_now()

    if stat.frequency == CountStat.DAY:
        delta = timedelta(days=1)
    else:
        delta = timedelta(hours=1)

    start_time = now - (periods * delta)
    query = output_table.objects.filter(
        property=property,
        end_time__gt=start_time,
    )

    if realm is not None and hasattr(output_table, "realm_id"):
        query = query.filter(realm_id=realm.id)

    values = list(query.order_by("end_time").values_list("value", flat=True))

    if len(values) < 2:
        return {"trend": "insufficient_data", "values": values}

    first_value = values[0]
    last_value = values[-1]

    if first_value == 0:
        if last_value == 0:
            trend = "stable"
        else:
            trend = "increasing"
    else:
        change_pct = ((last_value - first_value) / first_value) * 100
        if change_pct > 10:
            trend = "increasing"
        elif change_pct < -10:
            trend = "decreasing"
        else:
            trend = "stable"

    return {
        "trend": trend,
        "values": values,
        "first_value": first_value,
        "last_value": last_value,
        "periods_analyzed": len(values),
    }


def get_stats_by_frequency(frequency: str) -> list[CountStat]:
    return [stat for stat in ALL_COUNT_STATS.values() if stat.frequency == frequency]
