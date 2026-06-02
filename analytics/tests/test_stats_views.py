from datetime import datetime, timedelta, timezone

from django.utils.timezone import now as timezone_now
from typing_extensions import override

from analytics.lib.counts import COUNT_STATS, CountStat
from analytics.lib.time_utils import time_range
from analytics.models import FillState, RealmCount, StreamCount, UserCount
from analytics.views.stats import (
    get_time_series_by_subgroup,
    rewrite_client_arrays,
    sort_by_totals,
    sort_client_labels,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.timestamp import ceiling_to_day, ceiling_to_hour, datetime_to_timestamp
from zerver.models import Client
from zerver.models.realms import get_realm


class TestStatsEndpoint(ZulipTestCase):
    def test_stats(self) -> None:
        self.user = self.example_user("hamlet")
        self.login_user(self.user)
        result = self.client_get("/stats")
        self.assertEqual(result.status_code, 200)
        # Check that we get something back
        self.assert_in_response("Zulip analytics for", result)

    def test_guest_user_cant_access_stats(self) -> None:
        self.user = self.example_user("polonius")
        self.login_user(self.user)
        result = self.client_get("/stats")
        self.assert_json_error(result, "Not allowed for guest users", 400)

        result = self.client_get("/json/analytics/chart_data")
        self.assert_json_error(result, "Not allowed for guest users", 400)

    def test_stats_for_realm(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get("/stats/realm/zulip/")
        self.assertEqual(result.status_code, 302)

        result = self.client_get("/stats/realm/not_existing_realm/")
        self.assertEqual(result.status_code, 302)

        user = self.example_user("hamlet")
        user.is_staff = True
        user.save(update_fields=["is_staff"])

        result = self.client_get("/stats/realm/not_existing_realm/")
        self.assertEqual(result.status_code, 404)

        result = self.client_get("/stats/realm/zulip/")
        self.assertEqual(result.status_code, 200)
        self.assert_in_response("Zulip analytics for", result)

    def test_stats_for_installation(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get("/stats/installation")
        self.assertEqual(result.status_code, 302)

        user = self.example_user("hamlet")
        user.is_staff = True
        user.save(update_fields=["is_staff"])

        result = self.client_get("/stats/installation")
        self.assertEqual(result.status_code, 200)
        self.assert_in_response("Zulip analytics for", result)


class TestGetChartData(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.realm = get_realm("zulip")
        self.user = self.example_user("hamlet")
        self.stream_id = self.get_stream_id(self.get_streams(self.user)[0])
        self.login_user(self.user)
        self.end_times_hour = [
            ceiling_to_hour(self.realm.date_created) + timedelta(hours=i) for i in range(4)
        ]
        self.end_times_day = [
            ceiling_to_day(self.realm.date_created) + timedelta(days=i) for i in range(4)
        ]

    def data(self, i: int) -> list[int]:
        return [0, 0, i, 0]

    def insert_data(
        self, stat: CountStat, realm_subgroups: list[str | None], user_subgroups: list[str]
    ) -> None:
        if stat.frequency == CountStat.HOUR:
            insert_time = self.end_times_hour[2]
            fill_time = self.end_times_hour[-1]
        if stat.frequency == CountStat.DAY:
            insert_time = self.end_times_day[2]
            fill_time = self.end_times_day[-1]

        RealmCount.objects.bulk_create(
            RealmCount(
                property=stat.property,
                subgroup=subgroup,
                end_time=insert_time,
                value=100 + i,
                realm=self.realm,
            )
            for i, subgroup in enumerate(realm_subgroups)
        )
        UserCount.objects.bulk_create(
            UserCount(
                property=stat.property,
                subgroup=subgroup,
                end_time=insert_time,
                value=200 + i,
                realm=self.realm,
                user=self.user,
            )
            for i, subgroup in enumerate(user_subgroups)
        )
        StreamCount.objects.bulk_create(
            StreamCount(
                property=stat.property,
                subgroup=subgroup,
                end_time=insert_time,
                value=100 + i,
                stream_id=self.stream_id,
                realm=self.realm,
            )
            for i, subgroup in enumerate(realm_subgroups)
        )
        FillState.objects.create(property=stat.property, end_time=fill_time, state=FillState.DONE)

    def test_number_of_humans(self) -> None:
        stat = COUNT_STATS["realm_active_humans::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["1day_actives::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["active_users_audit:is_bot:day"]
        self.insert_data(stat, ["false"], [])
        result = self.client_get("/json/analytics/chart_data", {"chart_name": "number_of_humans"})
        data = self.assert_json_success(result)
        self.assertEqual(
            data,
            {
                "msg": "",
                "end_times": [datetime_to_timestamp(dt) for dt in self.end_times_day],
                "frequency": CountStat.DAY,
                "everyone": {
                    "_1day": self.data(100),
                    "_15day": self.data(100),
                    "all_time": self.data(100),
                },
                "display_order": None,
                "result": "success",
            },
        )

    def test_messages_sent_over_time(self) -> None:
        stat = COUNT_STATS["messages_sent:is_bot:hour"]
        self.insert_data(stat, ["true", "false"], ["false"])
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data,
            {
                "msg": "",
                "end_times": [datetime_to_timestamp(dt) for dt in self.end_times_hour],
                "frequency": CountStat.HOUR,
                "everyone": {"bot": self.data(100), "human": self.data(101)},
                "user": {"bot": self.data(0), "human": self.data(200)},
                "display_order": None,
                "result": "success",
            },
        )

    def test_messages_sent_by_message_type(self) -> None:
        stat = COUNT_STATS["messages_sent:message_type:day"]
        self.insert_data(
            stat, ["public_stream", "private_message"], ["public_stream", "private_stream"]
        )
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_by_message_type"}
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data,
            {
                "msg": "",
                "end_times": [datetime_to_timestamp(dt) for dt in self.end_times_day],
                "frequency": CountStat.DAY,
                "everyone": {
                    "Public channels": self.data(100),
                    "Private channels": self.data(0),
                    "Direct messages": self.data(101),
                    "Group direct messages": self.data(0),
                },
                "user": {
                    "Public channels": self.data(200),
                    "Private channels": self.data(201),
                    "Direct messages": self.data(0),
                    "Group direct messages": self.data(0),
                },
                "display_order": [
                    "Direct messages",
                    "Public channels",
                    "Private channels",
                    "Group direct messages",
                ],
                "result": "success",
            },
        )

    def test_messages_sent_by_client(self) -> None:
        stat = COUNT_STATS["messages_sent:client:day"]
        client1 = Client.objects.create(name="client 1")
        client2 = Client.objects.create(name="client 2")
        client3 = Client.objects.create(name="client 3")
        client4 = Client.objects.create(name="client 4")
        self.insert_data(
            stat,
            [str(client4.id), str(client3.id), str(client2.id)],
            [str(client3.id), str(client1.id)],
        )
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_by_client"}
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data,
            {
                "msg": "",
                "end_times": [datetime_to_timestamp(dt) for dt in self.end_times_day],
                "frequency": CountStat.DAY,
                "everyone": {
                    "client 4": self.data(100),
                    "client 3": self.data(101),
                    "client 2": self.data(102),
                },
                "user": {"client 3": self.data(200), "client 1": self.data(201)},
                "display_order": ["client 1", "client 2", "client 3", "client 4"],
                "result": "success",
            },
        )

    def test_messages_read_over_time(self) -> None:
        stat = COUNT_STATS["messages_read::hour"]
        self.insert_data(stat, [None], [])
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_read_over_time"}
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data,
            {
                "msg": "",
                "end_times": [datetime_to_timestamp(dt) for dt in self.end_times_hour],
                "frequency": CountStat.HOUR,
                "everyone": {"read": self.data(100)},
                "user": {"read": self.data(0)},
                "display_order": None,
                "result": "success",
            },
        )

    def test_messages_sent_by_stream(self) -> None:
        stat = COUNT_STATS["messages_in_stream:is_bot:day"]
        self.insert_data(stat, ["true", "false"], [])

        result = self.client_get(
            f"/json/analytics/chart_data/stream/{self.stream_id}",
            {
                "chart_name": "messages_sent_by_stream",
            },
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data,
            {
                "msg": "",
                "end_times": [datetime_to_timestamp(dt) for dt in self.end_times_day],
                "frequency": CountStat.DAY,
                "everyone": {"bot": self.data(100), "human": self.data(101)},
                "display_order": None,
                "result": "success",
            },
        )

        result = self.api_get(
            self.example_user("polonius"),
            f"/api/v1/analytics/chart_data/stream/{self.stream_id}",
            {
                "chart_name": "messages_sent_by_stream",
            },
        )
        self.assert_json_error(result, "Not allowed for guest users")

        # Verify we correctly forbid access to stats of streams in other realms.
        result = self.api_get(
            self.mit_user("sipbtest"),
            f"/api/v1/analytics/chart_data/stream/{self.stream_id}",
            {
                "chart_name": "messages_sent_by_stream",
            },
            subdomain="zephyr",
        )
        self.assert_json_error(result, "Invalid channel ID")

    def test_include_empty_subgroups(self) -> None:
        FillState.objects.create(
            property="realm_active_humans::day",
            end_time=self.end_times_day[0],
            state=FillState.DONE,
        )
        result = self.client_get("/json/analytics/chart_data", {"chart_name": "number_of_humans"})
        data = self.assert_json_success(result)
        self.assertEqual(data["everyone"], {"_1day": [0], "_15day": [0], "all_time": [0]})
        self.assertFalse("user" in data)

        FillState.objects.create(
            property="messages_sent:is_bot:hour",
            end_time=self.end_times_hour[0],
            state=FillState.DONE,
        )
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        data = self.assert_json_success(result)
        self.assertEqual(data["everyone"], {"human": [0], "bot": [0]})
        self.assertEqual(data["user"], {"human": [0], "bot": [0]})

        FillState.objects.create(
            property="messages_sent:message_type:day",
            end_time=self.end_times_day[0],
            state=FillState.DONE,
        )
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_by_message_type"}
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data["everyone"],
            {
                "Public channels": [0],
                "Private channels": [0],
                "Direct messages": [0],
                "Group direct messages": [0],
            },
        )
        self.assertEqual(
            data["user"],
            {
                "Public channels": [0],
                "Private channels": [0],
                "Direct messages": [0],
                "Group direct messages": [0],
            },
        )

        FillState.objects.create(
            property="messages_sent:client:day",
            end_time=self.end_times_day[0],
            state=FillState.DONE,
        )
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_by_client"}
        )
        data = self.assert_json_success(result)
        self.assertEqual(data["everyone"], {})
        self.assertEqual(data["user"], {})

    def test_start_and_end(self) -> None:
        stat = COUNT_STATS["realm_active_humans::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["1day_actives::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["active_users_audit:is_bot:day"]
        self.insert_data(stat, ["false"], [])
        end_time_timestamps = [datetime_to_timestamp(dt) for dt in self.end_times_day]

        # valid start and end
        result = self.client_get(
            "/json/analytics/chart_data",
            {
                "chart_name": "number_of_humans",
                "start": end_time_timestamps[1],
                "end": end_time_timestamps[2],
            },
        )
        data = self.assert_json_success(result)
        self.assertEqual(data["end_times"], end_time_timestamps[1:3])
        self.assertEqual(
            data["everyone"], {"_1day": [0, 100], "_15day": [0, 100], "all_time": [0, 100]}
        )

        # start later then end
        result = self.client_get(
            "/json/analytics/chart_data",
            {
                "chart_name": "number_of_humans",
                "start": end_time_timestamps[2],
                "end": end_time_timestamps[1],
            },
        )
        self.assert_json_error_contains(result, "Start time is later than")

    def test_min_length(self) -> None:
        stat = COUNT_STATS["realm_active_humans::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["1day_actives::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["active_users_audit:is_bot:day"]
        self.insert_data(stat, ["false"], [])
        # test min_length is too short to change anything
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "number_of_humans", "min_length": 2}
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data["end_times"], [datetime_to_timestamp(dt) for dt in self.end_times_day]
        )
        self.assertEqual(
            data["everyone"],
            {"_1day": self.data(100), "_15day": self.data(100), "all_time": self.data(100)},
        )
        # test min_length larger than filled data
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "number_of_humans", "min_length": 5}
        )
        data = self.assert_json_success(result)
        end_times = [
            ceiling_to_day(self.realm.date_created) + timedelta(days=i) for i in range(-1, 4)
        ]
        self.assertEqual(data["end_times"], [datetime_to_timestamp(dt) for dt in end_times])
        self.assertEqual(
            data["everyone"],
            {
                "_1day": [0, *self.data(100)],
                "_15day": [0, *self.data(100)],
                "all_time": [0, *self.data(100)],
            },
        )

    def test_non_existent_chart(self) -> None:
        result = self.client_get("/json/analytics/chart_data", {"chart_name": "does_not_exist"})
        self.assert_json_error_contains(result, "Unknown chart name")

    def test_analytics_not_running(self) -> None:
        realm = get_realm("zulip")

        self.assertEqual(FillState.objects.count(), 0)

        realm.date_created = timezone_now() - timedelta(days=3)
        realm.save(update_fields=["date_created"])
        with self.assertLogs(level="WARNING") as m:
            result = self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
            )
            self.assertEqual(
                m.output,
                [
                    f"WARNING:root:User from realm zulip attempted to access /stats, but the computed start time: {realm.date_created} (creation of realm or installation) is later than the computed end time: 0001-01-01 00:00:00+00:00 (last successful analytics update). Is the analytics cron job running?"
                ],
            )

        self.assert_json_error_contains(result, "No analytics data available")

        realm.date_created = timezone_now() - timedelta(days=1, hours=2)
        realm.save(update_fields=["date_created"])
        with self.assertLogs(level="WARNING") as m:
            result = self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
            )
            self.assertEqual(
                m.output,
                [
                    f"WARNING:root:User from realm zulip attempted to access /stats, but the computed start time: {realm.date_created} (creation of realm or installation) is later than the computed end time: 0001-01-01 00:00:00+00:00 (last successful analytics update). Is the analytics cron job running?"
                ],
            )

        self.assert_json_error_contains(result, "No analytics data available")

        realm.date_created = timezone_now() - timedelta(days=1, minutes=10)
        realm.save(update_fields=["date_created"])
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        self.assert_json_success(result)

        realm.date_created = timezone_now() - timedelta(hours=10)
        realm.save(update_fields=["date_created"])
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        self.assert_json_success(result)

        end_time = timezone_now() - timedelta(days=5)
        fill_state = FillState.objects.create(
            property="messages_sent:is_bot:hour", end_time=end_time, state=FillState.DONE
        )

        realm.date_created = timezone_now() - timedelta(days=3)
        realm.save(update_fields=["date_created"])
        with self.assertLogs(level="WARNING") as m:
            result = self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
            )
            self.assertEqual(
                m.output,
                [
                    f"WARNING:root:User from realm zulip attempted to access /stats, but the computed start time: {realm.date_created} (creation of realm or installation) is later than the computed end time: {end_time} (last successful analytics update). Is the analytics cron job running?"
                ],
            )

        self.assert_json_error_contains(result, "No analytics data available")

        realm.date_created = timezone_now() - timedelta(days=1, minutes=10)
        realm.save(update_fields=["date_created"])
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        self.assert_json_success(result)

        end_time = timezone_now() - timedelta(days=2)
        fill_state.end_time = end_time
        fill_state.save(update_fields=["end_time"])

        realm.date_created = timezone_now() - timedelta(days=3)
        realm.save(update_fields=["date_created"])
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        self.assert_json_success(result)

        realm.date_created = timezone_now() - timedelta(days=1, hours=2)
        realm.save(update_fields=["date_created"])
        with self.assertLogs(level="WARNING") as m:
            result = self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
            )
            self.assertEqual(
                m.output,
                [
                    f"WARNING:root:User from realm zulip attempted to access /stats, but the computed start time: {realm.date_created} (creation of realm or installation) is later than the computed end time: {end_time} (last successful analytics update). Is the analytics cron job running?"
                ],
            )

        self.assert_json_error_contains(result, "No analytics data available")

        realm.date_created = timezone_now() - timedelta(days=1, minutes=10)
        realm.save(update_fields=["date_created"])
        result = self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        self.assert_json_success(result)

    def test_get_chart_data_for_realm(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get(
            "/json/analytics/chart_data/realm/zulip", {"chart_name": "number_of_humans"}
        )
        self.assert_json_error(result, "Must be an server administrator", 400)

        user = self.example_user("hamlet")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        stat = COUNT_STATS["realm_active_humans::day"]
        self.insert_data(stat, [None], [])

        result = self.client_get(
            "/json/analytics/chart_data/realm/not_existing_realm",
            {"chart_name": "number_of_humans"},
        )
        self.assert_json_error(result, "Invalid organization", 400)

        result = self.client_get(
            "/json/analytics/chart_data/realm/zulip", {"chart_name": "number_of_humans"}
        )
        self.assert_json_success(result)

    def test_get_chart_data_for_installation(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get(
            "/json/analytics/chart_data/installation", {"chart_name": "number_of_humans"}
        )
        self.assert_json_error(result, "Must be an server administrator", 400)

        user = self.example_user("hamlet")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        stat = COUNT_STATS["realm_active_humans::day"]
        self.insert_data(stat, [None], [])

        result = self.client_get(
            "/json/analytics/chart_data/installation", {"chart_name": "number_of_humans"}
        )
        self.assert_json_success(result)


class TestGetChartDataHelpers(ZulipTestCase):
    def test_sort_by_totals(self) -> None:
        empty: list[int] = []
        value_arrays = {"c": [0, 1], "a": [9], "b": [1, 1, 1], "d": empty}
        self.assertEqual(sort_by_totals(value_arrays), ["a", "b", "c", "d"])

    def test_sort_client_labels(self) -> None:
        data = {
            "everyone": {"a": [16], "c": [15], "b": [14], "e": [13], "d": [12], "h": [11]},
            "user": {"a": [6], "b": [5], "d": [4], "e": [3], "f": [2], "g": [1]},
        }
        self.assertEqual(sort_client_labels(data), ["a", "b", "c", "d", "e", "f", "g", "h"])


class TestTimeRange(ZulipTestCase):
    def test_time_range(self) -> None:
        HOUR = timedelta(hours=1)
        DAY = timedelta(days=1)

        a_time = datetime(2016, 3, 14, 22, 59, tzinfo=timezone.utc)
        floor_hour = datetime(2016, 3, 14, 22, tzinfo=timezone.utc)
        floor_day = datetime(2016, 3, 14, tzinfo=timezone.utc)

        # test start == end
        self.assertEqual(time_range(a_time, a_time, CountStat.HOUR, None), [])
        self.assertEqual(time_range(a_time, a_time, CountStat.DAY, None), [])
        # test start == end == boundary, and min_length == 0
        self.assertEqual(time_range(floor_hour, floor_hour, CountStat.HOUR, 0), [floor_hour])
        self.assertEqual(time_range(floor_day, floor_day, CountStat.DAY, 0), [floor_day])
        # test start and end on different boundaries
        self.assertEqual(
            time_range(floor_hour, floor_hour + HOUR, CountStat.HOUR, None),
            [floor_hour, floor_hour + HOUR],
        )
        self.assertEqual(
            time_range(floor_day, floor_day + DAY, CountStat.DAY, None),
            [floor_day, floor_day + DAY],
        )
        # test min_length
        self.assertEqual(
            time_range(floor_hour, floor_hour + HOUR, CountStat.HOUR, 4),
            [floor_hour - 2 * HOUR, floor_hour - HOUR, floor_hour, floor_hour + HOUR],
        )
        self.assertEqual(
            time_range(floor_day, floor_day + DAY, CountStat.DAY, 4),
            [floor_day - 2 * DAY, floor_day - DAY, floor_day, floor_day + DAY],
        )


class TestMapArrays(ZulipTestCase):
    def test_map_arrays(self) -> None:
        a = {
            "desktop app 1.0": [1, 2, 3],
            "desktop app 2.0": [10, 12, 13],
            "desktop app 3.0": [21, 22, 23],
            "website": [1, 2, 3],
            "ZulipiOS": [1, 2, 3],
            "ZulipElectron": [2, 5, 7],
            "ZulipMobile": [1, 2, 3],
            "ZulipMobile/flutter": [1, 1, 1],
            "ZulipFlutter": [1, 1, 1],
            "ZulipPython": [1, 2, 3],
            "API: Python": [1, 2, 3],
            "SomethingRandom": [4, 5, 6],
            "ZulipGitHubWebhook": [7, 7, 9],
            "ZulipAndroid": [64, 63, 65],
            "ZulipTerminal": [9, 10, 11],
        }
        result = rewrite_client_arrays(a)
        self.assertEqual(
            result,
            {
                "Old desktop app": [32, 36, 39],
                "Ancient iOS app": [1, 2, 3],
                "Desktop app": [2, 5, 7],
                "Old mobile app (React Native)": [1, 2, 3],
                "Mobile app (Flutter)": [2, 2, 2],
                "Web app": [1, 2, 3],
                "Python API": [2, 4, 6],
                "SomethingRandom": [4, 5, 6],
                "GitHub webhook": [7, 7, 9],
                "Ancient Android app": [64, 63, 65],
                "Terminal app": [9, 10, 11],
            },
        )


class TestStatsPerformance(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.realm = get_realm("zulip")
        self.user = self.example_user("hamlet")
        self.stream_id = self.get_stream_id(self.get_streams(self.user)[0])
        self.login_user(self.user)
        self.end_times_hour = [
            ceiling_to_hour(self.realm.date_created) + timedelta(hours=i) for i in range(4)
        ]
        self.end_times_day = [
            ceiling_to_day(self.realm.date_created) + timedelta(days=i) for i in range(4)
        ]

    def insert_data(
        self, stat: CountStat, realm_subgroups: list[str | None], user_subgroups: list[str]
    ) -> None:
        if stat.frequency == CountStat.HOUR:
            insert_time = self.end_times_hour[2]
            fill_time = self.end_times_hour[-1]
        if stat.frequency == CountStat.DAY:
            insert_time = self.end_times_day[2]
            fill_time = self.end_times_day[-1]

        RealmCount.objects.bulk_create(
            RealmCount(
                property=stat.property,
                subgroup=subgroup,
                end_time=insert_time,
                value=100 + i,
                realm=self.realm,
            )
            for i, subgroup in enumerate(realm_subgroups)
        )
        UserCount.objects.bulk_create(
            UserCount(
                property=stat.property,
                subgroup=subgroup,
                end_time=insert_time,
                value=200 + i,
                realm=self.realm,
                user=self.user,
            )
            for i, subgroup in enumerate(user_subgroups)
        )
        StreamCount.objects.bulk_create(
            StreamCount(
                property=stat.property,
                subgroup=subgroup,
                end_time=insert_time,
                value=100 + i,
                stream_id=self.stream_id,
                realm=self.realm,
            )
            for i, subgroup in enumerate(realm_subgroups)
        )
        FillState.objects.create(property=stat.property, end_time=fill_time, state=FillState.DONE)

    def test_get_time_series_by_subgroup_query_count(self) -> None:
        stat = COUNT_STATS["messages_sent:is_bot:hour"]
        self.insert_data(stat, ["true", "false"], ["false"])
        subgroup_to_label = {"false": "human", "true": "bot"}
        with self.assert_database_query_count(1):
            get_time_series_by_subgroup(
                stat,
                RealmCount,
                self.realm.id,
                self.end_times_hour,
                subgroup_to_label,
                True,
            )

    def test_get_time_series_by_subgroup_query_count_with_many_subgroups(self) -> None:
        stat = COUNT_STATS["messages_sent:is_bot:hour"]
        num_subgroups = 20
        realm_subgroups = [f"subgroup_{i}" for i in range(num_subgroups)]
        RealmCount.objects.bulk_create(
            RealmCount(
                property=stat.property,
                subgroup=subgroup,
                end_time=self.end_times_hour[2],
                value=i,
                realm=self.realm,
            )
            for i, subgroup in enumerate(realm_subgroups)
        )
        FillState.objects.create(
            property=stat.property, end_time=self.end_times_hour[-1], state=FillState.DONE
        )
        subgroup_to_label = {s: s for s in realm_subgroups}
        with self.assert_database_query_count(1):
            get_time_series_by_subgroup(
                stat,
                RealmCount,
                self.realm.id,
                self.end_times_hour,
                subgroup_to_label,
                True,
            )

    def test_get_time_series_by_subgroup_query_count_with_many_end_times(self) -> None:
        stat = COUNT_STATS["1day_actives::day"]
        end_times = [
            ceiling_to_day(self.realm.date_created) + timedelta(days=i) for i in range(365)
        ]
        RealmCount.objects.bulk_create(
            RealmCount(
                property=stat.property,
                subgroup=None,
                end_time=end_time,
                value=i,
                realm=self.realm,
            )
            for i, end_time in enumerate(end_times)
        )
        FillState.objects.create(
            property=stat.property, end_time=end_times[-1], state=FillState.DONE
        )
        with self.assert_database_query_count(1):
            get_time_series_by_subgroup(
                stat,
                RealmCount,
                self.realm.id,
                end_times,
                {None: "active"},
                True,
            )

    def test_chart_data_number_of_humans_query_count(self) -> None:
        stat = COUNT_STATS["realm_active_humans::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["1day_actives::day"]
        self.insert_data(stat, [None], [])
        stat = COUNT_STATS["active_users_audit:is_bot:day"]
        self.insert_data(stat, ["false"], [])
        self.client_get("/json/analytics/chart_data", {"chart_name": "number_of_humans"})
        with self.assert_database_query_count(6, keep_cache_warm=True):
            self.client_get("/json/analytics/chart_data", {"chart_name": "number_of_humans"})

    def test_chart_data_messages_sent_over_time_query_count(self) -> None:
        stat = COUNT_STATS["messages_sent:is_bot:hour"]
        self.insert_data(stat, ["true", "false"], ["false"])
        self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        with self.assert_database_query_count(3, keep_cache_warm=True):
            self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
            )

    def test_chart_data_messages_sent_by_message_type_query_count(self) -> None:
        stat = COUNT_STATS["messages_sent:message_type:day"]
        self.insert_data(
            stat, ["public_stream", "private_message"], ["public_stream", "private_stream"]
        )
        self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_by_message_type"}
        )
        with self.assert_database_query_count(3, keep_cache_warm=True):
            self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_by_message_type"}
            )

    def test_chart_data_messages_sent_by_client_query_count(self) -> None:
        stat = COUNT_STATS["messages_sent:client:day"]
        Client.objects.create(name="client 1")
        Client.objects.create(name="client 2")
        self.insert_data(stat, ["1", "2"], ["1"])
        self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_by_client"}
        )
        with self.assert_database_query_count(4, keep_cache_warm=True):
            self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_by_client"}
            )

    def test_chart_data_messages_read_over_time_query_count(self) -> None:
        stat = COUNT_STATS["messages_read::hour"]
        self.insert_data(stat, [None], [])
        self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_read_over_time"}
        )
        with self.assert_database_query_count(3, keep_cache_warm=True):
            self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_read_over_time"}
            )

    def test_chart_data_query_count_does_not_scale_with_data_volume(self) -> None:
        stat = COUNT_STATS["messages_sent:is_bot:hour"]
        for i in range(10):
            RealmCount.objects.create(
                property=stat.property,
                subgroup="false",
                end_time=self.end_times_hour[2] + timedelta(hours=i),
                value=100 + i,
                realm=self.realm,
            )
            RealmCount.objects.create(
                property=stat.property,
                subgroup="true",
                end_time=self.end_times_hour[2] + timedelta(hours=i),
                value=200 + i,
                realm=self.realm,
            )
        FillState.objects.create(
            property=stat.property, end_time=self.end_times_hour[-1], state=FillState.DONE
        )
        self.client_get(
            "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
        )
        with self.assert_database_query_count(3, keep_cache_warm=True):
            self.client_get(
                "/json/analytics/chart_data", {"chart_name": "messages_sent_over_time"}
            )

    def test_rewrite_client_arrays_large_input(self) -> None:
        num_clients = 500
        num_time_points = 365
        input_data = {
            f"client_{i}": [i] * num_time_points for i in range(num_clients)
        }
        result = rewrite_client_arrays(input_data)
        self.assertEqual(len(result), num_clients)
        for array in result.values():
            self.assert_length(array, num_time_points)

    def test_rewrite_client_arrays_with_many_collisions(self) -> None:
        num_variants = 100
        num_time_points = 365
        input_data = {
            f"desktop app {i}.0": [i] * num_time_points for i in range(num_variants)
        }
        result = rewrite_client_arrays(input_data)
        self.assertIn("Old desktop app", result)
        expected_total = sum(range(num_variants))
        self.assertEqual(result["Old desktop app"][0], expected_total)
        self.assert_length(result["Old desktop app"], num_time_points)

    def test_sort_by_totals_large_input(self) -> None:
        num_entries = 1000
        num_time_points = 365
        value_arrays = {
            f"label_{i}": [i] * num_time_points for i in range(num_entries)
        }
        result = sort_by_totals(value_arrays)
        self.assert_length(result, num_entries)
        self.assertEqual(result[0], f"label_{num_entries - 1}")
        self.assertEqual(result[-1], "label_0")

    def test_sort_client_labels_large_input(self) -> None:
        num_clients = 200
        data = {
            "everyone": {f"client_{i}": [num_clients - i] for i in range(num_clients)},
            "user": {f"client_{i}": [num_clients - i] for i in range(num_clients)},
        }
        result = sort_client_labels(data)
        self.assert_length(result, num_clients)
        self.assertEqual(result[0], "client_0")

    def test_time_range_large_day_span(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        result = time_range(start, end, CountStat.DAY, None)
        expected_length = (end - start).days + 1
        self.assert_length(result, expected_length)
        self.assertEqual(result[0], start)
        self.assertEqual(result[-1], end)

    def test_time_range_large_hour_span(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = time_range(start, end, CountStat.HOUR, None)
        expected_length = int((end - start).total_seconds() // 3600) + 1
        self.assert_length(result, expected_length)
        self.assertEqual(result[0], start)
        self.assertEqual(result[-1], end)

    def test_time_range_with_large_min_length(self) -> None:
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 2, tzinfo=timezone.utc)
        result = time_range(start, end, CountStat.DAY, 1000)
        self.assert_length(result, 1000)
        self.assertEqual(result[-1], end)
