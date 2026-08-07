"""core/priority.py — Priority軸管理

CYCLE12再定義 + CYCLE12.7.4更新:
Priority = 利用実績とフィードバックで動的に決まる値。
初期値0.5固定（CYCLE12.7: 新規記憶の公平性確保）。
自動増進上限0.9、boost上限1.0。鮮度減衰なし。
record_accessではPriorityを変動させない（CYCLE12.7: 正のフィードバックループ解消）。
宣言的重要度はLayer軸に一本化し、Priorityからは除外。

CYCLE19.4（A5-3）追加: 消えたことが見える消え方にする。
検索スコアは `text_score × context_affinity × layer_weight × priority` の掛け算なので、
P=0.0 の記憶は final_score=0 となり search/engine.py で除外される。
このとき `archived` は立たないため、memory_status は生きていると数え続ける。
そこで下限で沈める（案a）のではなく、
閾値まで落ちた記憶を「archive候補」として利用者に知らせる（案b）。
archiveするかは人が決める（memory_unarchive で戻せる）。
"""

from __future__ import annotations

import math
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

# Priority値の丸め桁数（CYCLE19.4）
# recalculate は round(...,4) しているのに apply_* は丸めていなかったため、
# dismissを重ねると誤差が積もり（0.5から3回で 0.20000000000000004）、
# 閾値判定も「P=0.0への到達」も成立しなかった。両者の桁を揃える。
_PRIORITY_DIGITS = 4

# archive候補として提示する閾値（CYCLE19.4 / A5-3・暫定0.2）
# 初期値0.5からdismiss（-0.10）3回でここに達する。
ARCHIVE_CANDIDATE_THRESHOLD = 0.2

# ここまで落ちると検索結果に一切出なくなる（掛け算の0）
SEARCH_INVISIBLE_PRIORITY = 0.0


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
        return round(min(current + 0.05, _AUTO_PRIORITY_CAP), _PRIORITY_DIGITS)

    def apply_boost(self, current: float) -> float:
        """ユーザー明示フィードバック。+0.10、上限1.0。"""
        return round(min(current + 0.10, 1.0), _PRIORITY_DIGITS)

    def apply_dismiss(self, current: float) -> float:
        """-0.10、下限0.0。

        CYCLE19.4: 丸めを入れた。丸めないと誤差が積もり、
        閾値（0.2）にも下限（0.0）にも一致せず、recalculate の結果ともずれる。
        """
        return round(max(current - 0.10, 0.0), _PRIORITY_DIGITS)

    def is_archive_candidate(self, priority: float) -> bool:
        """archive候補の閾値まで落ちているか（CYCLE19.4 / A5-3）。"""
        return priority <= ARCHIVE_CANDIDATE_THRESHOLD

    def is_search_invisible(self, priority: float) -> bool:
        """検索結果に出なくなっているか（CYCLE19.4 / A5-3）。

        `archived` とは独立。archivedが立たないまま検索から消えている記憶は、
        memory_status では生きていると数えられる——これが「黙って消える」の実体。
        """
        return priority <= SEARCH_INVISIBLE_PRIORITY

    def dismisses_until_invisible(self, priority: float) -> int:
        """あと何回 dismiss すると検索から消えるか（CYCLE19.4 / A5-3）。"""
        if self.is_search_invisible(priority):
            return 0
        return max(math.ceil(round(priority / 0.10, _PRIORITY_DIGITS)), 1)

    def recalculate(self, event_counts: "EventCounts") -> float:
        """イベント履歴からPriorityを再導出する（CYCLE12.7.4: score_version=3）。

        mark_used × +0.05 + boost × +0.10 - dismiss × -0.10
        自動増進分(mark_used)は0.9上限、boost分は1.0上限。
        access_countはスコアリングから除外（CYCLE12.7: 正のフィードバックループ解消）。

        CYCLE19.4: 下限・丸めは apply_dismiss と同じにする。
        Priorityはイベント履歴からの導出値なので、
        逐次適用（apply_*）と再導出（recalculate）が同じ値に着地しなければ、
        recalculate を回すたびに archive候補の判定が変わってしまう。
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
