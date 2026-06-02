from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from unittest import mock

from django.db.models import Model, QuerySet, models

from analytics.lib.counts import (
    COUNT_STATS,
    CountStat,
    DependentCountStat,
    LoggingCountStat,
    do_aggregate_to_summary_table,
    do_drop_all_analytics_tables,
    do_drop_single_stat,
    do_fill_count_stat_at_hour,
    do_increment_logging_stat,
    get_count_stats,
    process_count_stat,
    sql_data_collector,
)
from analytics.models import (
    BaseCount,
    FillState,
    InstallationCount,
    RealmCount,
    StreamCount,
    UserCount,
    installation_epoch,
)
from analytics.tests.test_counts import AnalyticsTestCase
from zerver.lib.test_classes import ZulipTestCase


def clear_analytics_tables() -> None:
    """Clear all analytics tables for a clean test state."""
    do_drop_all_analytics_tables()


def get_count_values(
    table: type[BaseCount],
    property_name: str,
    end_time: datetime | None = None,
) -> list[tuple[Any, int]]:
    """
    Get all count values for a given property from a *Count table.
    
    Returns a list of (identifier, value) tuples, where identifier is
    the relevant ID (user_id, stream_id, realm_id, etc.).
    """
    query = table.objects.filter(property=property_name)
    if end_time is not None:
        query = query.filter(end_time=end_time)
    
    if table == UserCount:
        return [(row.user_id, row.value) for row in query]
    elif table == StreamCount:
        return [(row.stream_id, row.value) for row in query]
    elif table in [RealmCount, InstallationCount]:
        return [(row.realm_id if hasattr(row, "realm_id") else None, row.value) for row in query]
    else:
        return [(row.id, row.value) for row in query]


def create_test_user_activity(
    user_ids: list[int],
    start_time: datetime,
    end_time: datetime,
    interval: timedelta = timedelta(minutes=30),
) -> None:
    """
    Create test user activity intervals for multiple users.
    
    This helps in testing stats that depend on UserActivityInterval.
    """
    from zerver.models import UserActivityInterval, UserProfile
    
    users = UserProfile.objects.filter(id__in=user_ids)
    for user in users:
        current_time = start_time
        while current_time < end_time:
            UserActivityInterval.objects.create(
                user_profile=user,
                start=current_time,
                end=min(current_time + interval, end_time),
            )
            current_time += interval


def fill_stats_until(stat_name: str, end_time: datetime) -> None:
    """
    Fill a specific CountStat until the given end_time.
    
    This is a convenience function for testing.
    """
    stat = COUNT_STATS[stat_name]
    process_count_stat(stat, end_time)


def create_test_attachments(
    realm_id: int,
    user_id: int,
    sizes: list[int],
    create_time: datetime,
) -> None:
    """
    Create test attachments with specified sizes for upload quota testing.
    """
    from zerver.models import Attachment, Realm, UserProfile
    
    realm = Realm.objects.get(id=realm_id)
    user = UserProfile.objects.get(id=user_id)
    
    for i, size in enumerate(sizes):
        Attachment.objects.create(
            file_name=f"test_file_{i}.txt",
            path_id=f"test/path/{i}.txt",
            owner=user,
            realm=realm,
            size=size,
            create_time=create_time,
            content_type="text/plain",
        )


def get_time_travel_context(target_time: datetime) -> Iterator[None]:
    """
    A context manager for time traveling in tests.
    
    This is a wrapper around time_machine.travel for convenience.
    """
    import time_machine
    with time_machine.travel(target_time, tick=False):
        yield


def assert_count_equals(
    table: type[BaseCount],
    property_name: str,
    expected_value: int,
    **filters: Any,
) -> None:
    """
    Assert that a specific count in a *Count table has the expected value.
    
    Example:
        assert_count_equals(UserCount, "messages_sent:is_bot:hour", 5, user=user)
    """
    result = table.objects.filter(property=property_name, **filters).aggregate(sum_value=models.Sum("value"))
    assert result["sum_value"] == expected_value, f"Expected {expected_value}, got {result['sum_value']}"


# Initialize commonly used test times for convenience
TIME_ZERO = datetime(1988, 3, 14, tzinfo=timezone.utc)
TIME_ONE_HOUR = TIME_ZERO + timedelta(hours=1)
TIME_ONE_DAY = TIME_ZERO + timedelta(days=1)
TIME_ONE_WEEK = TIME_ZERO + timedelta(days=7)


def batch_create_messages(
    sender_id: int,
    recipient_ids: list[int],
    topic: str = "test_topic",
    content: str = "test message",
    count: int = 1,
    date_sent: datetime = TIME_ZERO,
) -> list[int]:
    """
    Batch create messages for a sender to multiple recipients.
    
    Returns the list of created message IDs.
    """
    from zerver.models import Message, Recipient, UserProfile
    
    sender = UserProfile.objects.get(id=sender_id)
    recipients = Recipient.objects.filter(id__in=recipient_ids)
    
    message_ids = []
    for _ in range(count):
        for recipient in recipients:
            message = Message.objects.create(
                sender=sender,
                recipient=recipient,
                topic_name=topic,
                content=content,
                date_sent=date_sent,
                realm_id=sender.realm_id,
            )
            message_ids.append(message.id)
    
    return message_ids


def create_test_realm_with_users(
    realm_name: str,
    num_users: int = 3,
    is_bot: bool = False,
) -> tuple[Any, list[Any]]:
    """
    Create a test realm with specified number of users.
    
    Returns (realm, list_of_users).
    """
    from zerver.actions.create_realm import do_create_realm
    from zerver.lib.create_user import create_user
    
    realm = do_create_realm(
        string_id=f"test-realm-{realm_name}",
        name=f"Test Realm {realm_name}",
        date_created=TIME_ZERO - timedelta(days=2),
    )
    
    users = []
    for i in range(num_users):
        user = create_user(
            email=f"test-user-{i}@{realm_name}.com",
            password="testpassword",
            realm=realm,
            full_name=f"Test User {i}",
            is_bot=is_bot,
        )
        users.append(user)
    
    return realm, users


def get_realm_count_summary(
    realm_id: int,
    property_name: str,
    end_time: datetime,
) -> int | None:
    """
    Get a summarized count for a specific realm and property.
    
    Returns the total value for the given realm and property.
    """
    result = RealmCount.objects.filter(
        realm_id=realm_id,
        property=property_name,
        end_time=end_time,
    ).aggregate(total_value=models.Sum("value"))
    return result["total_value"]


def create_test_streams(
    realm_id: int,
    num_streams: int = 2,
    invite_only: bool = False,
) -> list[Any]:
    """
    Create test streams in a realm.
    
    Returns the list of created streams.
    """
    from zerver.models import Recipient, Realm, Stream
    from zerver.lib.streams import get_default_values_for_stream_permission_group_settings
    
    realm = Realm.objects.get(id=realm_id)
    streams = []
    
    for i in range(num_streams):
        default_settings = get_default_values_for_stream_permission_group_settings(realm)
        stream = Stream.objects.create(
            name=f"test-stream-{i}",
            realm=realm,
            date_created=TIME_ZERO - timedelta(hours=1),
            invite_only=invite_only,
            **default_settings,
        )
        # Create recipient for the stream
        Recipient.objects.create(type_id=stream.id, type=Recipient.STREAM)
        streams.append(stream)
    
    return streams


def simulate_user_activity(
    user_id: int,
    start_time: datetime,
    end_time: datetime,
    num_messages: int = 10,
) -> None:
    """
    Simulate a user being active by creating messages and activity intervals.
    """
    from zerver.actions.user_activity import update_user_activity_interval
    from zerver.models import Message, Recipient, Stream, UserProfile
    
    user = UserProfile.objects.get(id=user_id)
    streams = Stream.objects.filter(realm_id=user.realm_id)[:1]
    
    if not streams:
        # Create a test stream if none exists
        from zerver.lib.streams import get_default_values_for_stream_permission_group_settings
        stream = Stream.objects.create(
            name="test-stream",
            realm=user.realm,
            date_created=start_time - timedelta(hours=1),
            **get_default_values_for_stream_permission_group_settings(user.realm),
        )
        Recipient.objects.create(type_id=stream.id, type=Recipient.STREAM)
        streams = [stream]
    
    recipient = streams[0].recipient
    
    # Create activity interval
    update_user_activity_interval(user, start_time, end_time)
    
    # Create messages
    current_time = start_time
    time_step = (end_time - start_time) / max(num_messages, 1)
    for i in range(num_messages):
        Message.objects.create(
            sender=user,
            recipient=recipient,
            topic_name=f"test-topic-{i}",
            content=f"Test message {i}",
            date_sent=current_time,
            realm_id=user.realm_id,
        )
        current_time += time_step

