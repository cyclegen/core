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

    def collect(
        self, period_days: int = 30, memories: list | None = None
    ) -> DiagnosticsReport:
        """全メトリクスを集計する。

        Args:
            memories: 読み込み済みの記憶（archived込み）。診断のように複数の集計を
                続けて回す場面で、同じ `load_all` を何度も走らせないために渡す
                （CYCLE19.6）。
        """
        all_memories = (
            memories if memories is not None
            else self.persistence.load_all(include_archived=True)
        )
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

        # CYCLE19.6（A4）: 検索回数を分母にしたシグナル率。
        # boost_rate（フィードバック内訳）では「そもそも判断を返していない」が見えない。
        dismiss_rate = dismiss_count / total_searches if total_searches > 0 else 0.0
        boost_rate_per_search = boost_count / total_searches if total_searches > 0 else 0.0

        # CYCLE19.6（A4）: embeddingの出所の内訳（CYCLE19.2 の embedding_model 列）
        embedding_model_dist: dict[str, int] = {}
        for m in active:
            key = m.embedding_model or ""
            embedding_model_dist[key] = embedding_model_dist.get(key, 0) + 1

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

        # source別の内訳（CYCLE19.3 FR035 方向3）
        # 経路ごとに信頼度が違うので、混ぜたまま1つの数字にしない。
        # source未指定（memory_mark_used の直接呼び出し）は "explicit" として数える。
        #
        # ★ここでの既定値は recall_used 限定の判断である（CYCLE20.5 / FR062①-a）。
        #   recall_used は「利用者が使ったと言った」以外の入口が無かったので既定に倒せる。
        #   dismiss / boost / archive は違う——source を書き始めたのはCYCLE20.5からで、
        #   それ以前のイベントには検証や掃除が混ざっている（CYCLE19.7の実発火確認5回など）。
        #   FR062①-b（MS2）でこれらを集計するときは、source の無いものを explicit に
        #   倒さず "unknown" として別に数えること（FR062 受入条件3・CYCLE19.2 A8の規律）。
        recall_used_by_source: dict[str, int] = {}
        for e in recall_used_events:
            src = e.details.get("source") or "explicit"
            recall_used_by_source[src] = recall_used_by_source.get(src, 0) + 1

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
                dismiss_rate=dismiss_rate,
                boost_rate_per_search=boost_rate_per_search,
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
                recall_used_by_source=recall_used_by_source,
            ),
            embedding_model_distribution=embedding_model_dist,
            warnings=warnings,
        )
