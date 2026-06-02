from datetime import datetime
from typing import Any

from django.db.models import Sum

from analytics.models import (
    InstallationCount,
    RealmCount,
    UserCount,
)


def validate_count_aggregation(property: str, end_time: datetime) -> dict[str, Any]:
    installation_value = (
        InstallationCount.objects.filter(property=property, end_time=end_time)
        .aggregate(total=Sum("value"))["total"]
        or 0
    )

    realm_total = (
        RealmCount.objects.filter(property=property, end_time=end_time)
        .aggregate(total=Sum("value"))["total"]
        or 0
    )

    user_total = (
        UserCount.objects.filter(property=property, end_time=end_time)
        .aggregate(total=Sum("value"))["total"]
        or 0
    )

    realm_ids = (
        RealmCount.objects.filter(property=property, end_time=end_time)
        .values_list("realm_id", flat=True)
        .distinct()
    )

    realm_details = []
    for realm_id in realm_ids:
        realm_value = (
            RealmCount.objects.filter(
                property=property, end_time=end_time, realm_id=realm_id
            ).aggregate(total=Sum("value"))["total"]
            or 0
        )
        realm_user_value = (
            UserCount.objects.filter(
                property=property, end_time=end_time, realm_id=realm_id
            ).aggregate(total=Sum("value"))["total"]
            or 0
        )
        realm_details.append(
            {
                "realm_id": realm_id,
                "realm_count_value": realm_value,
                "user_count_value": realm_user_value,
                "consistent": realm_value >= realm_user_value,
            }
        )

    all_realm_user_consistent = all(detail["consistent"] for detail in realm_details)

    return {
        "property": property,
        "end_time": end_time.isoformat(),
        "installation_count_value": installation_value,
        "realm_count_total_value": realm_total,
        "user_count_total_value": user_total,
        "installation_realm_match": installation_value == realm_total,
        "realm_user_consistent": all_realm_user_consistent,
        "realm_details": realm_details,
    }