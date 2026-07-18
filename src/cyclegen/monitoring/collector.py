"""monitoring/collector.py — 診断レポート収集

実装計画書§9: event_logとmemory_indexを集計してDiagnosticsReportを生成する。
"""

from __future__ import annotations

from cyclegen.core.priority import PriorityManager
from cyclegen.models import (
    DiagnosticsReport,
    EventType,
    PrecisionStats,
    PromotionStats,
    SearchStats,
)
from cyclegen.monitoring.event_log import EventLogger
from cyclegen.persistence.base import PersistenceAdapter


class DiagnosticsCollector:
    """event_logとmemory_indexを集計してDiagnosticsReportを生成する。"""

    def __init__(self, event_logger: EventLogger, persistence: PersistenceAdapter):
        self.event_logger = event_logger
        self.persistence = persistence
        self._priority_mgr = PriorityManager()

    def collect(self, period_days: int = 30) -> DiagnosticsReport:
        """全メトリクスを集計する。"""
        all_memories = self.persistence.load_all(include_archived=True)
        active = [m for m in all_memories if not m.archived]

        # Layer分布
        layer_dist: dict[int, int] = {}
        for m in active:
            layer_dist[m.coordinates.layer] = layer_dist.get(m.coordinates.layer, 0) + 1

        # Priority分布
        priority_dist: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "archive": 0}
        for m in active:
            cls = self._priority_mgr.classify(m.coordinates.priority)
            priority_dist[cls] = priority_dist.get(cls, 0) + 1

        # Priority詳細分布（CYCLE12: 値別）
        priority_detail: dict[str, int] = {}
        for m in active:
            key = f"{m.coordinates.priority:.1f}"
            priority_detail[key] = priority_detail.get(key, 0) + 1

        # access_count=0 の件数（CYCLE12）
        access_count_zero = sum(1 for m in active if m.access_count == 0)

        # Context分布
        context_dist: dict[str, int] = {}
        for m in active:
            ctx = m.coordinates.context
            context_dist[ctx] = context_dist.get(ctx, 0) + 1

        # イベント集計
        events = self.event_logger.get_events(since_days=period_days)

        search_events = [e for e in events if e.event_type == EventType.SEARCH]
        boost_events = [e for e in events if e.event_type == EventType.BOOST]
        dismiss_events = [e for e in events if e.event_type == EventType.DISMISS]
        promote_events = [e for e in events if e.event_type == EventType.PROMOTE]

        total_searches = len(search_events)
        avg_score = 0.0
        if search_events:
            scores = [
                e.details.get("top_score", 0) for e in search_events
            ]
            avg_score = sum(scores) / len(scores) if scores else 0.0

        boost_count = len(boost_events)
        dismiss_count = len(dismiss_events)
        total_feedback = boost_count + dismiss_count
        boost_rate = boost_count / total_feedback if total_feedback > 0 else 0.0

        # 昇格統計
        total_promotions = len(promote_events)
        avg_post_access = 0.0
        if promote_events:
            promoted_ids = {e.memory_id for e in promote_events if e.memory_id}
            accesses = []
            for m in all_memories:
                if m.id in promoted_ids:
                    accesses.append(m.access_count)
            avg_post_access = sum(accesses) / len(accesses) if accesses else 0.0

        # Memory Precision（CYCLE6.2）
        recall_used_events = [e for e in events if e.event_type == EventType.RECALL_USED]
        total_recalled = sum(
            len(e.details.get("recalled_ids", []))
            for e in search_events
        )
        used_count = len(recall_used_events)
        precision_rate = used_count / total_recalled if total_recalled > 0 else 0.0

        # セッション別Precision（CYCLE13.2 FR031 P1）
        # session_idでsearch（recalled）とrecall_used（used）を紐付け、
        # セッション単位で「返したうち実際に使われた割合」を算出する。
        # 全体precision_rateと異なり、用済みIDが該当セッションのrecalled集合に
        # 含まれるかを照合するため、計測漏れに頑健で重複利用にも左右されにくい。
        session_recalled: dict[str, set] = {}
        for e in search_events:
            sid = e.details.get("session_id")
            if not sid:
                continue
            session_recalled.setdefault(sid, set()).update(
                e.details.get("recalled_ids", [])
            )
        session_used: dict[str, set] = {}
        for e in recall_used_events:
            sid = e.details.get("session_id")
            if sid and e.memory_id:
                session_used.setdefault(sid, set()).add(e.memory_id)
        session_precision: dict[str, float] = {}
        for sid, recalled in session_recalled.items():
            if recalled:
                used_in = len(session_used.get(sid, set()) & recalled)
                session_precision[sid] = used_in / len(recalled)
        session_count = len(session_precision)
        avg_session_precision = (
            sum(session_precision.values()) / session_count
            if session_count > 0
            else 0.0
        )

        # 偏り警告（CYCLE12）
        warnings: list[str] = []
        if active:
            total = len(active)
            for cls_name, count in priority_dist.items():
                if count / total > 0.8:
                    warnings.append(f"Priority偏り: {cls_name}が{count}/{total}件（{count/total:.0%}）を占めています")
            if access_count_zero / total > 0.7:
                warnings.append(f"未利用記憶: access_count=0が{access_count_zero}/{total}件（{access_count_zero/total:.0%}）です")

        return DiagnosticsReport(
            total_memories=len(active),
            layer_distribution=layer_dist,
            priority_distribution=priority_dist,
            priority_detail=priority_detail,
            context_distribution=context_dist,
            pinned_count=sum(1 for m in active if m.pinned),
            archived_count=sum(1 for m in all_memories if m.archived),
            access_count_zero=access_count_zero,
            search_stats=SearchStats(
                total_searches=total_searches,
                avg_score=avg_score,
                boost_count=boost_count,
                dismiss_count=dismiss_count,
                boost_rate=boost_rate,
            ),
            promotion_stats=PromotionStats(
                total_promotions=total_promotions,
                avg_post_promotion_access=avg_post_access,
            ),
            precision_stats=PrecisionStats(
                total_recalled=total_recalled,
                used_count=used_count,
                precision_rate=precision_rate,
                session_count=session_count,
                avg_session_precision=avg_session_precision,
                session_precision=session_precision,
            ),
            warnings=warnings,
        )
