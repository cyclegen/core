"""monitoring/idle_recall.py — 空振り常連（返却されるのに一度も使われない記憶）の検出

CYCLE19.5（A5-2）。

**1回の空振りは判断材料にならないが、N回の空振りは判断材料になる。**

条件: 累積返却回数 ≥ N かつ mark_used = 0 かつ boost = 0

閾値Nは固定値にしない。その利用者のmark_used捕捉率pから毎回導出する:

    N = ceil(log(0.05) / log(1 − p))

「本当は有用な記憶がN回返って一度もマークされない確率」を5%に抑える値。
捕捉率が上がるほどNは下がり、提案は細かくなる
（p=10%→N=30 / p=30%→N=9 / p=50%→N=5）。

★ 捕捉率が低いうちは提案しない。
   負のシグナル（使われなかった）は、正のシグナル（使った）より精度が高くなれない。
   捕捉率0の環境で「使われていない」を根拠にすると、全件が容疑者になる。

このモジュールは**読むだけ**で、記憶を書き換えない。提示するのは候補であって、
dismissするか・分割するか・何もしないかは人が決める（HITL）。
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field

from cyclegen.models import EventType, Memory
from cyclegen.monitoring.event_log import EventLogger
from cyclegen.persistence.base import PersistenceAdapter

# 誤爆の許容率（有用な記憶を空振り常連と誤判定する確率の上限）
FALSE_POSITIVE_TARGET = 0.05

# 閾値Nの下限。捕捉率が高くても、これ未満では判断材料にしない
MIN_THRESHOLD = 5

# 捕捉率を推定するのに最低限必要な返却スロット数。
# これを下回る環境では提案しない（空の庭に剪定の提案をしない）。
MIN_SLOTS_FOR_ESTIMATE = 100

# 累積で判断するので全期間を見る（event_logのAPIが日数指定のため大きな値を渡す）
ALL_TIME_DAYS = 36500

# 「分割」を推奨する本文長の倍率（コーパス中央値の何倍を超えたら長文とみなすか）
LONG_CONTENT_RATIO = 2.0

# 提案の種別
SUGGEST_SPLIT = "split"
SUGGEST_DISMISS = "dismiss"


def derive_threshold(capture_rate: float) -> int | None:
    """mark_used捕捉率から閾値Nを導出する。

    導出できない（捕捉率0＝正のシグナルが1件も無い）場合は None を返す。
    """
    if capture_rate <= 0.0:
        return None
    if capture_rate >= 1.0:
        return MIN_THRESHOLD
    n = math.ceil(math.log(FALSE_POSITIVE_TARGET) / math.log(1.0 - capture_rate))
    return max(n, MIN_THRESHOLD)


@dataclass
class _EventScan:
    """event_logの走査結果（記憶本体を読む前の中間状態）。"""

    recall_counts: Counter
    total_slots: int
    used_count: int
    used_ids: set[str]
    boosted_ids: set[str]

    @property
    def capture_rate(self) -> float:
        return self.used_count / self.total_slots if self.total_slots else 0.0


@dataclass
class IdleCandidate:
    """空振り常連の候補1件。"""

    memory: Memory
    recall_count: int
    suggestion: str  # SUGGEST_SPLIT / SUGGEST_DISMISS

    @property
    def content_length(self) -> int:
        return len(self.memory.content)


@dataclass
class IdleRecallReport:
    """空振り常連の検出結果。

    候補が0件でも、なぜ0件なのか（閾値が導出できない／データが薄い／該当なし）が
    分かるように、算出に使った値をすべて持たせる。
    """

    capture_rate: float = 0.0
    threshold: int | None = None
    total_slots: int = 0
    used_count: int = 0
    candidates: list[IdleCandidate] = field(default_factory=list)
    occupied_slots: int = 0  # 候補が占めている返却スロット数
    skipped_unknown: int = 0  # Personal層に存在しないID（Org層の記憶など）
    reason_no_threshold: str = ""  # 閾値を出せなかった理由（出せた場合は空）

    # 全期間の「記憶ID → 返却回数」。候補が0件でも必ず入る。
    # CYCLE19.6（A4）の診断がここから返却カバー率を出す
    # ——同じevent_logを二度走査しないため。
    recall_counts: Counter = field(default_factory=Counter)

    @property
    def occupied_ratio(self) -> float:
        return self.occupied_slots / self.total_slots if self.total_slots else 0.0

    @property
    def distinct_recalled(self) -> int:
        """一度でも検索で返ったことのある記憶の数（全期間）。"""
        return len(self.recall_counts)

    def returned_count(self, existing_ids: set[str]) -> int:
        """いま存在する記憶のうち、一度でも検索で返ったことがある件数。"""
        return sum(1 for mid in existing_ids if self.recall_counts.get(mid, 0) > 0)

    def unreturned_ratio(self, existing_ids: set[str]) -> float:
        """一度も検索で返っていない記憶の割合（CYCLE19.6 / C3）。

        「未利用（access_count=0）」とは別物。あちらは返ったあと使われたかで、
        こちらは**そもそも土俵に上がっていない**記憶の割合。

        いま存在するIDの集合を受け取る（件数ではなく集合）。
        event_logには削除済み・Org層のIDも残っているので、
        件数だけで引き算すると未返却率を実態より低く見せてしまう。
        """
        if not existing_ids:
            return 0.0
        return (len(existing_ids) - self.returned_count(existing_ids)) / len(existing_ids)

    def top_share(self, total_memories: int, ratio: float = 0.01) -> tuple[int, float]:
        """上位n%の記憶が返却スロットのどれだけを占めるか（CYCLE19.6 / C4）。

        戻り値は (対象件数, 占有率)。検索が固着していないかを見る。
        """
        if total_memories <= 0 or self.total_slots <= 0:
            return 0, 0.0
        top_n = max(1, int(total_memories * ratio))
        top_slots = sum(c for _, c in self.recall_counts.most_common(top_n))
        return top_n, top_slots / self.total_slots


class IdleRecallAnalyzer:
    """event_log と記憶本体から空振り常連を検出する。

    CYCLE19.6 の `memory_diagnostics` 拡張はこの集計に相乗りする。
    """

    def __init__(self, event_logger: EventLogger, persistence: PersistenceAdapter):
        self.event_logger = event_logger
        self.persistence = persistence

    def analyze(
        self,
        threshold_override: int | None = None,
        memories: list[Memory] | None = None,
    ) -> IdleRecallReport:
        """空振り常連を検出する（同期版）。

        Args:
            threshold_override: 閾値Nを明示指定する（検証・再現用）。
                省略時は捕捉率から導出する。
            memories: 読み込み済みの記憶。診断のように複数の集計を続けて回す場面で、
                同じ `load_all` を何度も走らせないために渡す（CYCLE19.6）。
        """
        scan = self._scan(self.event_logger.get_events(since_days=ALL_TIME_DAYS))
        report, target_ids = self._prepare(scan, threshold_override)
        if not target_ids:
            return report
        if memories is None:
            memories = self.persistence.load_all(include_archived=False)
        return self._finalize(report, scan, target_ids, memories)

    async def async_analyze(
        self,
        threshold_override: int | None = None,
        memories: list[Memory] | None = None,
    ) -> IdleRecallReport:
        """空振り常連を検出する（非同期版）。"""
        scan = self._scan(
            await self.event_logger.async_get_events(since_days=ALL_TIME_DAYS)
        )
        report, target_ids = self._prepare(scan, threshold_override)
        if not target_ids:
            return report
        if memories is None:
            memories = await self.persistence.async_load_all(include_archived=False)
        return self._finalize(report, scan, target_ids, memories)

    # ------------------------------------------------------------------
    # 3段に分けてあるのは、**言うことが無いと分かった時点で記憶を読まないため**。
    # 記憶の読み込み（load_all）はストアが育つほど重くなる（母艦2,161件で約1秒）。
    # 候補が出ない環境——使い始めの利用者や、健全に運用できている利用者——が
    # 毎CYCLEその費用を払う理由はない。
    # ------------------------------------------------------------------

    @staticmethod
    def _scan(events: list) -> "_EventScan":
        """event_logだけを見る（記憶本体は読まない）。"""
        recall_counts: Counter = Counter()
        total_slots = 0
        used_ids: set[str] = set()
        boosted_ids: set[str] = set()
        used_count = 0

        for e in events:
            if e.event_type == EventType.SEARCH:
                ids = e.details.get("recalled_ids", []) or []
                total_slots += len(ids)
                recall_counts.update(ids)
            elif e.event_type == EventType.RECALL_USED:
                used_count += 1
                if e.memory_id:
                    used_ids.add(e.memory_id)
            elif e.event_type == EventType.BOOST:
                if e.memory_id:
                    boosted_ids.add(e.memory_id)

        return _EventScan(
            recall_counts=recall_counts,
            total_slots=total_slots,
            used_count=used_count,
            used_ids=used_ids,
            boosted_ids=boosted_ids,
        )

    @staticmethod
    def _prepare(
        scan: "_EventScan", threshold_override: int | None
    ) -> tuple[IdleRecallReport, list[str]]:
        """閾値を決め、閾値を超えたIDを洗い出す。記憶本体はまだ読まない。

        戻り値の第2要素が空なら、この先の処理は不要（記憶を読む必要もない）。
        """
        report = IdleRecallReport(
            capture_rate=scan.capture_rate,
            total_slots=scan.total_slots,
            used_count=scan.used_count,
            recall_counts=scan.recall_counts,
        )

        if threshold_override is not None:
            report.threshold = threshold_override
        elif scan.total_slots < MIN_SLOTS_FOR_ESTIMATE:
            # 使い始めの環境。捕捉率の推定が定まらないうちは何も提案しない。
            report.reason_no_threshold = (
                f"返却スロットが{scan.total_slots}件（{MIN_SLOTS_FOR_ESTIMATE}件未満）＝"
                "捕捉率の推定が定まらないため提案しません"
            )
            return report, []
        else:
            report.threshold = derive_threshold(scan.capture_rate)
            if report.threshold is None:
                report.reason_no_threshold = (
                    "mark_usedが1件も記録されていないため、"
                    "「使われていない」を判断材料にできません"
                )
                return report, []

        target_ids = [
            mid
            for mid, count in scan.recall_counts.most_common()
            # most_common は降順なので、閾値を下回ったらそこで終わり
            if count >= report.threshold
            and mid not in scan.used_ids
            and mid not in scan.boosted_ids
        ]
        return report, target_ids

    def _finalize(
        self,
        report: IdleRecallReport,
        scan: "_EventScan",
        target_ids: list[str],
        memories: list[Memory],
    ) -> IdleRecallReport:
        """記憶本体を突き合わせて候補を確定する。書き換えは一切しない。"""
        # archivedは既に片付いているので候補にしないし、中央値の母数にも入れない。
        # 呼び出し側がarchived込みで渡してくることがある（CYCLE19.6の診断）。
        active = [m for m in memories if not m.archived]
        by_id = {m.id: m for m in active}
        median_length = self._median_length(active)

        candidates: list[IdleCandidate] = []
        for mid in target_ids:
            memory = by_id.get(mid)
            if memory is None:
                # Personal層に無い記憶（Org層／archived／削除済み）。
                # dismissも分割もできないので候補にしない。Core構成では常に0件。
                report.skipped_unknown += 1
                continue
            if memory.pinned:
                # 利用者が明示的に「重要」と言ったものは提案しない。
                # 明示の判断は、累積の統計より強い。
                continue
            candidates.append(
                IdleCandidate(
                    memory=memory,
                    recall_count=scan.recall_counts[mid],
                    suggestion=self._suggest(memory, median_length),
                )
            )

        report.candidates = candidates
        report.occupied_slots = sum(c.recall_count for c in candidates)
        return report

    @staticmethod
    def _median_length(memories: list[Memory]) -> float:
        if not memories:
            return 0.0
        return statistics.median(len(m.content) for m in memories)

    @staticmethod
    def _suggest(memory: Memory, median_length: float) -> str:
        """長すぎる記憶は「内容が悪い」のではなく「何にでも当たる」可能性がある。

        その場合の正しい対処は dismiss ではなく分割。
        dismiss一択にすると良い記憶を沈める。
        """
        if median_length > 0 and len(memory.content) > median_length * LONG_CONTENT_RATIO:
            return SUGGEST_SPLIT
        return SUGGEST_DISMISS
