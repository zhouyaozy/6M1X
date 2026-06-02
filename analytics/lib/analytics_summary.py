import csv
import io
import json
import logging
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Avg, Count, Max, Min, Sum
from django.utils.timezone import now as timezone_now

from analytics.lib.counts import COUNT_STATS, CountStat
from analytics.lib.time_utils import time_range
from analytics.models import (
    FillState,
    InstallationCount,
    RealmCount,
)
from zerver.models import Realm

logger = logging.getLogger("zulip.analytics")


class AnalyticsSummaryError(Exception):
    pass


class SummaryConfig:
    def __init__(
        self,
        stat_property: str,
        realm: Realm | None = None,
        days: int = 30,
    ) -> None:
        if stat_property not in COUNT_STATS:
            raise AnalyticsSummaryError(f"Unknown stat property: {stat_property}")
        self.stat = COUNT_STATS[stat_property]
        self.realm = realm
        self.days = days

    @property
    def end_time(self) -> datetime:
        last_fill = self.stat.last_successful_fill()
        if last_fill is not None:
            return last_fill
        return timezone_now()

    @property
    def start_time(self) -> datetime:
        return self.end_time - timedelta(days=self.days)


def get_realm_summary(
    stat: CountStat,
    realm: Realm,
    start_time: datetime,
    end_time: datetime,
) -> dict[str, Any]:
    queryset = RealmCount.objects.filter(
        realm=realm,
        property=stat.property,
        end_time__gte=start_time,
        end_time__lte=end_time,
    )

    aggregates = queryset.aggregate(
        total=Sum("value"),
        average=Avg("value"),
        maximum=Max("value"),
        minimum=Min("value"),
        data_points=Count("id"),
    )

    subgroups = (
        queryset.values_list("subgroup", flat=True)
        .distinct()
        .order_by("subgroup")
    )
    subgroup_list = [sg for sg in subgroups if sg is not None]

    return {
        "realm_id": realm.id,
        "realm_name": realm.name,
        "realm_string_id": realm.string_id,
        "stat_property": stat.property,
        "stat_frequency": stat.frequency,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "total": aggregates["total"] or 0,
        "average": round(aggregates["average"] or 0, 2),
        "maximum": aggregates["maximum"] or 0,
        "minimum": aggregates["minimum"] or 0,
        "data_points": aggregates["data_points"],
        "subgroups": subgroup_list,
    }


def get_installation_summary(
    stat: CountStat,
    start_time: datetime,
    end_time: datetime,
) -> dict[str, Any]:
    queryset = InstallationCount.objects.filter(
        property=stat.property,
        end_time__gte=start_time,
        end_time__lte=end_time,
    )

    aggregates = queryset.aggregate(
        total=Sum("value"),
        average=Avg("value"),
        maximum=Max("value"),
        minimum=Min("value"),
        data_points=Count("id"),
    )

    subgroups = (
        queryset.values_list("subgroup", flat=True)
        .distinct()
        .order_by("subgroup")
    )
    subgroup_list = [sg for sg in subgroups if sg is not None]

    return {
        "stat_property": stat.property,
        "stat_frequency": stat.frequency,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "total": aggregates["total"] or 0,
        "average": round(aggregates["average"] or 0, 2),
        "maximum": aggregates["maximum"] or 0,
        "minimum": aggregates["minimum"] or 0,
        "data_points": aggregates["data_points"],
        "subgroups": subgroup_list,
    }


def get_fill_state_summary() -> list[dict[str, Any]]:
    results = []
    for property, stat in COUNT_STATS.items():
        fill_state = FillState.objects.filter(property=property).first()
        if fill_state is None:
            results.append(
                {
                    "property": property,
                    "frequency": stat.frequency,
                    "last_fill_time": None,
                    "state": "never_run",
                }
            )
        else:
            state_label = "done" if fill_state.state == FillState.DONE else "started"
            results.append(
                {
                    "property": property,
                    "frequency": stat.frequency,
                    "last_fill_time": fill_state.end_time.isoformat(),
                    "state": state_label,
                }
            )
    return results


def get_time_series_data(
    stat: CountStat,
    start_time: datetime,
    end_time: datetime,
    realm: Realm | None = None,
) -> dict[str, list[dict[str, Any]]]:
    end_times = time_range(start_time, end_time, stat.frequency, None)

    if realm is not None:
        queryset = RealmCount.objects.filter(
            realm=realm,
            property=stat.property,
            end_time__in=end_times,
        )
    else:
        queryset = InstallationCount.objects.filter(
            property=stat.property,
            end_time__in=end_times,
        )

    rows = queryset.values_list("subgroup", "end_time", "value").order_by("subgroup", "end_time")

    series_by_subgroup: dict[str, list[dict[str, Any]]] = {}
    for subgroup, end_time, value in rows:
        key = subgroup if subgroup is not None else "total"
        if key not in series_by_subgroup:
            series_by_subgroup[key] = []
        series_by_subgroup[key].append(
            {
                "end_time": end_time.isoformat(),
                "value": value,
            }
        )

    return series_by_subgroup


def compute_growth_rate(
    stat: CountStat,
    start_time: datetime,
    end_time: datetime,
    realm: Realm | None = None,
) -> dict[str, float | None]:
    if realm is not None:
        queryset = RealmCount.objects.filter(
            realm=realm,
            property=stat.property,
        )
    else:
        queryset = InstallationCount.objects.filter(
            property=stat.property,
        )

    midpoint = start_time + (end_time - start_time) / 2

    first_half = queryset.filter(
        end_time__gte=start_time,
        end_time__lt=midpoint,
    ).aggregate(total=Sum("value"))

    second_half = queryset.filter(
        end_time__gte=midpoint,
        end_time__lte=end_time,
    ).aggregate(total=Sum("value"))

    first_total = first_half["total"] or 0
    second_total = second_half["total"] or 0

    if first_total == 0:
        return {"growth_rate": None, "first_half_total": first_total, "second_half_total": second_total}

    growth_rate = (second_total - first_total) / first_total
    return {
        "growth_rate": round(growth_rate, 4),
        "first_half_total": first_total,
        "second_half_total": second_total,
    }


def export_summary_as_json(summary_data: dict[str, Any] | list[dict[str, Any]]) -> str:
    return json.dumps(summary_data, indent=2, default=str)


def export_summary_as_csv(
    summary_data: dict[str, Any] | list[dict[str, Any]],
) -> str:
    output = io.StringIO()
    if isinstance(summary_data, list):
        if len(summary_data) == 0:
            return ""
        writer = csv.DictWriter(output, fieldnames=summary_data[0].keys())
        writer.writeheader()
        writer.writerows(summary_data)
    else:
        writer = csv.DictWriter(output, fieldnames=summary_data.keys())
        writer.writeheader()
        writer.writerow(summary_data)
    return output.getvalue()


def generate_full_report(
    realm: Realm | None = None,
    days: int = 30,
    stat_properties: list[str] | None = None,
) -> OrderedDict:
    if stat_properties is not None:
        stats = [(p, COUNT_STATS[p]) for p in stat_properties if p in COUNT_STATS]
    else:
        stats = list(COUNT_STATS.items())

    now = timezone_now()
    start_time = now - timedelta(days=days)

    report: OrderedDict[str, Any] = OrderedDict()
    report["generated_at"] = now.isoformat()
    report["period_days"] = days
    report["start_time"] = start_time.isoformat()
    report["end_time"] = now.isoformat()

    fill_state_summary = get_fill_state_summary()
    report["fill_state_summary"] = fill_state_summary

    stat_summaries: list[dict[str, Any]] = []
    for property, stat in stats:
        last_fill = stat.last_successful_fill()
        effective_end = last_fill if last_fill is not None else now

        if realm is not None:
            summary = get_realm_summary(stat, realm, start_time, effective_end)
        else:
            summary = get_installation_summary(stat, start_time, effective_end)

        growth = compute_growth_rate(stat, start_time, effective_end, realm)
        summary["growth"] = growth
        stat_summaries.append(summary)

    report["stat_summaries"] = stat_summaries
    return report
