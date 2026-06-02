from datetime import datetime, timedelta, timezone
from typing import Any

from django.db.models import Sum

from analytics.lib.counts import CountStat
from analytics.models import (
    FillState,
    InstallationCount,
    RealmCount,
    StreamCount,
    UserCount,
)
from zerver.models import Realm, Stream, UserProfile


class AnalyticsTestHelper:
    @staticmethod
    def create_realm_count(
        realm: Realm,
        property: str,
        value: int,
        end_time: datetime,
        subgroup: str | None = None,
    ) -> RealmCount:
        return RealmCount.objects.create(
            realm=realm,
            property=property,
            subgroup=subgroup,
            end_time=end_time,
            value=value,
        )

    @staticmethod
    def create_user_count(
        user: UserProfile,
        property: str,
        value: int,
        end_time: datetime,
        subgroup: str | None = None,
    ) -> UserCount:
        return UserCount.objects.create(
            user=user,
            realm=user.realm,
            property=property,
            subgroup=subgroup,
            end_time=end_time,
            value=value,
        )

    @staticmethod
    def create_stream_count(
        stream: Stream,
        property: str,
        value: int,
        end_time: datetime,
        subgroup: str | None = None,
    ) -> StreamCount:
        return StreamCount.objects.create(
            stream=stream,
            realm=stream.realm,
            property=property,
            subgroup=subgroup,
            end_time=end_time,
            value=value,
        )

    @staticmethod
    def create_installation_count(
        property: str,
        value: int,
        end_time: datetime,
        subgroup: str | None = None,
    ) -> InstallationCount:
        return InstallationCount.objects.create(
            property=property,
            subgroup=subgroup,
            end_time=end_time,
            value=value,
        )

    @staticmethod
    def create_fill_state(
        property: str,
        end_time: datetime,
        state: int = FillState.DONE,
    ) -> FillState:
        return FillState.objects.create(
            property=property,
            end_time=end_time,
            state=state,
        )

    @staticmethod
    def generate_time_series(
        start_time: datetime,
        end_time: datetime,
        frequency: str = CountStat.HOUR,
        value_fn: callable | None = None,
    ) -> list[datetime]:
        if frequency == CountStat.HOUR:
            increment = timedelta(hours=1)
        else:
            increment = timedelta(days=1)

        times = []
        current = start_time
        while current <= end_time:
            times.append(current)
            current += increment
        return times

    @staticmethod
    def bulk_create_counts(
        counts_data: list[dict[str, Any]],
        table: type[RealmCount | UserCount | StreamCount],
    ) -> list[RealmCount | UserCount | StreamCount]:
        return table.objects.bulk_create([table(**data) for data in counts_data])

    @staticmethod
    def clear_analytics_data() -> None:
        UserCount.objects.all().delete()
        StreamCount.objects.all().delete()
        RealmCount.objects.all().delete()
        InstallationCount.objects.all().delete()
        FillState.objects.all().delete()

    @staticmethod
    def get_counts_by_property(
        table: type[RealmCount | UserCount | StreamCount],
        property: str,
        **kwargs: Any,
    ) -> list[RealmCount | UserCount | StreamCount]:
        return list(table.objects.filter(property=property, **kwargs))

    @staticmethod
    def sum_counts_by_property(
        table: type[RealmCount | UserCount | StreamCount],
        property: str,
        **kwargs: Any,
    ) -> int:
        result = table.objects.filter(property=property, **kwargs).aggregate(
            total_value=Sum("value")
        )
        return result["total_value"] or 0

    @staticmethod
    def verify_fill_state(
        property: str,
        expected_end_time: datetime,
        expected_state: int = FillState.DONE,
    ) -> bool:
        fill_state = FillState.objects.filter(property=property).first()
        if fill_state is None:
            return False
        return (
            fill_state.end_time == expected_end_time
            and fill_state.state == expected_state
        )
