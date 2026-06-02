
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from django.db import connection, models
from django.db.models import Q
from psycopg2.sql import SQL, Composable, Identifier, Literal

from analytics.lib.counts import COUNT_STATS, CountStat
from analytics.models import BaseCount, UserCount
from zerver.lib.timestamp import ceiling_to_day, floor_to_day, verify_UTC
from zerver.models import Message, Realm, Stream, UserProfile


class EngagementMetric:
    """用户参与度指标基类"""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"

    def __init__(
        self,
        name: str,
        description: str,
        weight: float = 1.0,
    ) -&gt; None:
        self.name = name
        self.description = description
        self.weight = weight

    def calculate(
        self,
        user: UserProfile,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; float:
        """计算指标值，子类必须实现"""
        raise NotImplementedError

    def normalize(self, value: float, max_value: float) -&gt; float:
        """标准化指标值到 [0, 1] 范围"""
        if max_value == 0:
            return 0.0
        return min(1.0, value / max_value)


class MessagesSentMetric(EngagementMetric):
    """用户发送消息指标"""

    def __init__(self) -&gt; None:
        super().__init__(
            name="messages_sent",
            description="用户在指定时间段内发送的消息数量",
            weight=0.3,
        )

    def calculate(
        self,
        user: UserProfile,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; float:
        count = Message.objects.filter(
            sender=user,
            date_sent__gte=start_time,
            date_sent__lt=end_time,
        ).count()
        return float(count)


class RepliesSentMetric(EngagementMetric):
    """用户回复消息指标"""

    def __init__(self) -&gt; None:
        super().__init__(
            name="replies_sent",
            description="用户在指定时间段内发送的回复数量",
            weight=0.25,
        )

    def calculate(
        self,
        user: UserProfile,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; float:
        count = Message.objects.filter(
            sender=user,
            date_sent__gte=start_time,
            date_sent__lt=end_time,
        ).exclude(
            Q(subject__isnull=True) | Q(subject="")
        ).count()
        return float(count)


class StreamsParticipatedMetric(EngagementMetric):
    """用户参与的流数量指标"""

    def __init__(self) -&gt; None:
        super().__init__(
            name="streams_participated",
            description="用户在指定时间段内参与的流数量",
            weight=0.2,
        )

    def calculate(
        self,
        user: UserProfile,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; float:
        streams = Message.objects.filter(
            sender=user,
            date_sent__gte=start_time,
            date_sent__lt=end_time,
        ).values_list("recipient__type_id", flat=True).distinct()
        return float(len(streams))


class ReactionsReceivedMetric(EngagementMetric):
    """用户收到的反应数量指标"""

    def __init__(self) -&gt; None:
        super().__init__(
            name="reactions_received",
            description="用户在指定时间段内收到的反应数量",
            weight=0.15,
        )

    def calculate(
        self,
        user: UserProfile,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; float:
        cursor = connection.cursor()
        cursor.execute(
            SQL("""
                SELECT COUNT(*)
                FROM zerver_reaction
                JOIN zerver_message ON zerver_reaction.message_id = zerver_message.id
                WHERE zerver_message.sender_id = %s
                  AND zerver_message.date_sent &gt;= %s
                  AND zerver_message.date_sent &lt; %s
            """),
            [user.id, start_time, end_time]
        )
        result = cursor.fetchone()
        cursor.close()
        return float(result[0]) if result else 0.0


class MentionsReceivedMetric(EngagementMetric):
    """用户收到的提及数量指标"""

    def __init__(self) -&gt; None:
        super().__init__(
            name="mentions_received",
            description="用户在指定时间段内收到的提及数量",
            weight=0.1,
        )

    def calculate(
        self,
        user: UserProfile,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; float:
        cursor = connection.cursor()
        cursor.execute(
            SQL("""
                SELECT COUNT(*)
                FROM zerver_usermessage
                WHERE zerver_usermessage.user_profile_id = %s
                  AND zerver_usermessage.flags &amp; 1 = 1
                  AND zerver_usermessage.message_id IN (
                      SELECT id FROM zerver_message
                      WHERE date_sent &gt;= %s AND date_sent &lt; %s
                  )
            """),
            [user.id, start_time, end_time]
        )
        result = cursor.fetchone()
        cursor.close()
        return float(result[0]) if result else 0.0


class EngagementScoreCalculator:
    """用户参与度分数计算器"""

    def __init__(self) -&gt; None:
        self.metrics: List[EngagementMetric] = [
            MessagesSentMetric(),
            RepliesSentMetric(),
            StreamsParticipatedMetric(),
            ReactionsReceivedMetric(),
            MentionsReceivedMetric(),
        ]

    def calculate_user_score(
        self,
        user: UserProfile,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; Dict[str, Any]:
        """计算单个用户的参与度分数"""
        metric_values = {}
        total_score = 0.0

        for metric in self.metrics:
            value = metric.calculate(user, start_time, end_time)
            metric_values[metric.name] = value

        max_values = self._calculate_max_values(
            user.realm, start_time, end_time
        )

        for metric in self.metrics:
            value = metric_values[metric.name]
            normalized_value = metric.normalize(value, max_values.get(metric.name, 0))
            total_score += normalized_value * metric.weight

        return {
            "user_id": user.id,
            "user_email": user.email,
            "total_score": round(total_score, 4),
            "metrics": metric_values,
            "start_time": start_time,
            "end_time": end_time,
        }

    def _calculate_max_values(
        self,
        realm: Realm,
        start_time: datetime,
        end_time: datetime,
    ) -&gt; Dict[str, float]:
        """计算所有指标的最大值，用于标准化"""
        max_values: Dict[str, float] = {}

        cursor = connection.cursor()

        cursor.execute(
            SQL("""
                SELECT COUNT(*) as count
                FROM zerver_message
                WHERE zerver_message.realm_id = %s
                  AND zerver_message.date_sent &gt;= %s
                  AND zerver_message.date_sent &lt; %s
                GROUP BY zerver_message.sender_id
                ORDER BY count DESC
                LIMIT 1
            """),
            [realm.id, start_time, end_time]
        )
        result = cursor.fetchone()
        max_values["messages_sent"] = float(result[0]) if result else 0.0

        cursor.execute(
            SQL("""
                SELECT COUNT(*) as count
                FROM zerver_message
                WHERE zerver_message.realm_id = %s
                  AND zerver_message.date_sent &gt;= %s
                  AND zerver_message.date_sent &lt; %s
                  AND zerver_message.subject IS NOT NULL
                  AND zerver_message.subject != ''
                GROUP BY zerver_message.sender_id
                ORDER BY count DESC
                LIMIT 1
            """),
            [realm.id, start_time, end_time]
        )
        result = cursor.fetchone()
        max_values["replies_sent"] = float(result[0]) if result else 0.0

        cursor.execute(
            SQL("""
                SELECT COUNT(DISTINCT zerver_recipient.type_id) as count
                FROM zerver_message
                JOIN zerver_recipient ON zerver_message.recipient_id = zerver_recipient.id
                WHERE zerver_message.realm_id = %s
                  AND zerver_message.date_sent &gt;= %s
                  AND zerver_message.date_sent &lt; %s
                GROUP BY zerver_message.sender_id
                ORDER BY count DESC
                LIMIT 1
            """),
            [realm.id, start_time, end_time]
        )
        result = cursor.fetchone()
        max_values["streams_participated"] = float(result[0]) if result else 0.0

        cursor.close()
        return max_values

    def calculate_realm_scores(
        self,
        realm: Realm,
        period: str = EngagementMetric.DAY,
        limit: Optional[int] = None,
    ) -&gt; List[Dict[str, Any]]:
        """计算整个组织的用户参与度分数"""
        verify_UTC(datetime.now(timezone.utc))
        end_time = ceiling_to_day(datetime.now(timezone.utc))

        if period == EngagementMetric.DAY:
            start_time = end_time - timedelta(days=1)
        elif period == EngagementMetric.WEEK:
            start_time = end_time - timedelta(weeks=1)
        elif period == EngagementMetric.MONTH:
            start_time = end_time - timedelta(days=30)
        else:
            raise ValueError(f"Unknown period: {period}")

        users = UserProfile.objects.filter(
            realm=realm,
            is_active=True,
            is_bot=False,
        )

        scores = []
        for user in users:
            score = self.calculate_user_score(user, start_time, end_time)
            scores.append(score)

        scores.sort(key=lambda x: x["total_score"], reverse=True)

        if limit is not None:
            scores = scores[:limit]

        return scores


def get_top_users(
    realm: Realm,
    period: str = EngagementMetric.DAY,
    limit: int = 10,
) -&gt; List[Dict[str, Any]]:
    """获取参与度最高的用户"""
    calculator = EngagementScoreCalculator()
    return calculator.calculate_realm_scores(realm, period, limit)


def get_user_engagement_trend(
    user: UserProfile,
    days: int = 30,
) -&gt; List[Dict[str, Any]]:
    """获取用户参与度趋势"""
    verify_UTC(datetime.now(timezone.utc))
    end_time = ceiling_to_day(datetime.now(timezone.utc))
    start_time = end_time - timedelta(days=days)

    calculator = EngagementScoreCalculator()
    trend = []

    current_date = end_time
    while current_date &gt; start_time:
        day_start = current_date - timedelta(days=1)
        score = calculator.calculate_user_score(user, day_start, current_date)
        trend.append(score)
        current_date = day_start

    trend.reverse()
    return trend


engagement_calculator = EngagementScoreCalculator()

