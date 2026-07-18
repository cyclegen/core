"""core/priority.py — Priority軸管理

CYCLE12再定義 + CYCLE12.7.4更新:
Priority = 利用実績とフィードバックで動的に決まる値。
初期値0.5固定（CYCLE12.7: 新規記憶の公平性確保）。
自動増進上限0.9、boost上限1.0。鮮度減衰なし。
record_accessではPriorityを変動させない（CYCLE12.7: 正のフィードバックループ解消）。
宣言的重要度はLayer軸に一本化し、Priorityからは除外。
"""

from __future__ import annotations

from dataclasses import dataclass


# Priority区分閾値（diagnostics表示用）
PRIORITY_THRESHOLDS = {
    "high": 0.8,  # 0.8-1.0
    "medium": 0.5,  # 0.5-0.79
    "low": 0.2,  # 0.2-0.49
    "archive": 0.0,  # 0.0-0.19
}

# 初期Priority（CYCLE12.7: 全件同一、新規記憶の公平性確保）
_DEFAULT_PRIORITY = 0.5

# 自動増進の上限（boostのみ1.0到達可能）
_AUTO_PRIORITY_CAP = 0.9


class PriorityManager:
    """Priority軸の管理クラス。

    CYCLE12再定義: Priorityは利用実績とフィードバックで動的に変動する。
    初期値は全件0.3。利用(record_access/mark_used)で自動増進（上限0.9）。
    boost(ユーザー明示)でのみ1.0に到達可能。
    """

    def estimate_initial(self, content: str) -> float:
        """初期Priorityを返す。

        CYCLE12.7: 全件0.5固定。新規記憶の公平性確保。
        """
        return _DEFAULT_PRIORITY

    def classify(self, priority: float) -> str:
        """Priority値を区分名に変換する。"""
        if priority >= PRIORITY_THRESHOLDS["high"]:
            return "high"
        if priority >= PRIORITY_THRESHOLDS["medium"]:
            return "medium"
        if priority >= PRIORITY_THRESHOLDS["low"]:
            return "low"
        return "archive"

    def apply_access_boost(self, current: float) -> float:
        """検索で返却された時の自動増進。

        CYCLE12.7.4: 廃止。record_accessではPriorityを変動させない。
        正のフィードバックループ解消のため。
        後方互換のためメソッドは残すが、値を変えずそのまま返す。
        """
        return current

    def apply_mark_used_boost(self, current: float) -> float:
        """実際に利用された時の自動増進。+0.05、上限0.9。"""
        return min(current + 0.05, _AUTO_PRIORITY_CAP)

    def apply_boost(self, current: float) -> float:
        """ユーザー明示フィードバック。+0.10、上限1.0。"""
        return min(current + 0.10, 1.0)

    def apply_dismiss(self, current: float) -> float:
        """-0.10、下限0.0。"""
        return max(current - 0.10, 0.0)

    def recalculate(self, event_counts: "EventCounts") -> float:
        """イベント履歴からPriorityを再導出する（CYCLE12.7.4: score_version=3）。

        mark_used × +0.05 + boost × +0.10 - dismiss × -0.10
        自動増進分(mark_used)は0.9上限、boost分は1.0上限。
        access_countはスコアリングから除外（CYCLE12.7: 正のフィードバックループ解消）。
        """
        # 自動増進分（mark_usedのみ、accessは除外）: 上限0.9
        auto_delta = event_counts.mark_used * 0.05
        auto_priority = min(_DEFAULT_PRIORITY + auto_delta, _AUTO_PRIORITY_CAP)

        # boost分: 上限1.0
        boost_delta = event_counts.boost * 0.10
        priority_with_boost = min(auto_priority + boost_delta, 1.0)

        # dismiss分: 下限0.0
        dismiss_delta = event_counts.dismiss * 0.10
        return max(round(priority_with_boost - dismiss_delta, 4), 0.0)


# 現在のスコアリングロジックバージョン
# v1=旧宣言的, v2=利用増進(CYCLE12), v3=access除外+初期値0.5(CYCLE12.7.4)
CURRENT_SCORE_VERSION = 3


@dataclass
class EventCounts:
    """Priority再計算に必要なイベント集計値。"""

    access: int = 0
    mark_used: int = 0
    boost: int = 0
    dismiss: int = 0
