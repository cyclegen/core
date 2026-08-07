"""test_dismiss_archive_candidate.py — CYCLE19.4（A5-3）dismiss下限＋archive候補提示

目的: 「消えたことが見える消え方にする」。
検索スコアは掛け算なので P=0.0 の記憶は検索から完全に消えるが、
`archived` は立たないため memory_status は生きていると数え続ける。
そこで閾値まで落ちた記憶を archive候補として知らせ、archiveするかは人が決める。
"""

from __future__ import annotations

import pytest

from cyclegen.config import DEFAULT_CONTEXTS
from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.memory_system import MemorySystem3D
from cyclegen.core.priority import (
    ARCHIVE_CANDIDATE_THRESHOLD,
    SEARCH_INVISIBLE_PRIORITY,
    EventCounts,
    PriorityManager,
)
from cyclegen.mcp.tools.memory import _format_dismiss_warning
from cyclegen.models import ContextDefinition
from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
from cyclegen.search.engine import SearchEngine


@pytest.fixture
def system(tmp_path) -> MemorySystem3D:
    persistence = MdWithSQLitePersistence(tmp_path)
    contexts = {
        name: ContextDefinition(**defn) for name, defn in DEFAULT_CONTEXTS.items()
    }
    sys = MemorySystem3D(
        persistence=persistence,
        layer_hierarchy=LayerHierarchy(),
        priority_manager=PriorityManager(),
        context_selector=ContextSelector(contexts),
        classifier=AutoLayerClassifier(),
        search_engine=SearchEngine(),
    )
    yield sys
    persistence.close()


class TestDismissRounding:
    """丸めが無いと閾値にも下限にも一致しなかった（CYCLE19.4で発見）。"""

    def test_three_dismisses_land_exactly_on_threshold(
        self, priority_manager: PriorityManager
    ):
        """0.5から3回で ちょうど0.2。丸めが無いと 0.20000000000000004 になる。"""
        p = 0.5
        for _ in range(3):
            p = priority_manager.apply_dismiss(p)
        assert p == 0.2

    def test_five_dismisses_reach_exactly_zero(self, priority_manager: PriorityManager):
        """0.5から5回で ちょうど0.0。丸めが無いと 2.77e-17 が残り、6回必要だった。"""
        p = 0.5
        for _ in range(5):
            p = priority_manager.apply_dismiss(p)
        assert p == 0.0

    def test_repeated_boost_lands_exactly_on_high_tier(
        self, priority_manager: PriorityManager
    ):
        """boost側にも同じ誤差があった。0.7999999999999999 は high と判定されない。"""
        p = 0.5
        for _ in range(3):
            p = priority_manager.apply_boost(p)
        assert p == 0.8
        assert priority_manager.classify(p) == "high"


class TestRecalculateConsistency:
    """受入条件3: Priorityはイベント履歴からの導出値なので、
    逐次適用と再導出が同じ値に着地しなければ、
    recalculate を回すたびに archive候補の判定が変わってしまう。"""

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 10])
    def test_sequential_dismiss_matches_recalculate(
        self, priority_manager: PriorityManager, n: int
    ):
        p = 0.5
        for _ in range(n):
            p = priority_manager.apply_dismiss(p)
        assert p == priority_manager.recalculate(EventCounts(dismiss=n))

    def test_candidate_judgement_survives_recalculate(
        self, priority_manager: PriorityManager
    ):
        """3回dismissした記憶は、再導出してもarchive候補のまま。"""
        p = 0.5
        for _ in range(3):
            p = priority_manager.apply_dismiss(p)
        recalculated = priority_manager.recalculate(EventCounts(dismiss=3))
        assert priority_manager.is_archive_candidate(p)
        assert priority_manager.is_archive_candidate(recalculated)


class TestThresholdPredicates:
    def test_archive_candidate_boundary(self, priority_manager: PriorityManager):
        assert priority_manager.is_archive_candidate(ARCHIVE_CANDIDATE_THRESHOLD)
        assert priority_manager.is_archive_candidate(0.1)
        assert priority_manager.is_archive_candidate(0.0)
        assert not priority_manager.is_archive_candidate(0.3)
        assert not priority_manager.is_archive_candidate(0.5)

    def test_search_invisible_boundary(self, priority_manager: PriorityManager):
        assert priority_manager.is_search_invisible(SEARCH_INVISIBLE_PRIORITY)
        assert not priority_manager.is_search_invisible(0.1)

    def test_dismisses_until_invisible(self, priority_manager: PriorityManager):
        assert priority_manager.dismisses_until_invisible(0.5) == 5
        assert priority_manager.dismisses_until_invisible(0.2) == 2
        assert priority_manager.dismisses_until_invisible(0.1) == 1
        assert priority_manager.dismisses_until_invisible(0.0) == 0

    def test_dismisses_until_invisible_never_zero_while_visible(
        self, priority_manager: PriorityManager
    ):
        """まだ見えている記憶に「あと0回」と言わない。"""
        assert priority_manager.dismisses_until_invisible(0.01) == 1


class TestDismissWarning:
    """受入条件1: dismissを繰り返してもP=0.0で黙って消えない。"""

    def test_no_warning_above_threshold(self, priority_manager: PriorityManager):
        assert _format_dismiss_warning("mem_x", 0.3, priority_manager) == ""

    def test_candidate_warning_at_threshold(self, priority_manager: PriorityManager):
        msg = _format_dismiss_warning("mem_x", 0.2, priority_manager)
        assert "archive候補" in msg
        assert "あと2回" in msg
        assert 'memory_archive("mem_x")' in msg
        assert "memory_unarchive" in msg  # 戻せることを必ず添える

    def test_invisible_warning_names_the_inconsistency(
        self, priority_manager: PriorityManager
    ):
        """検索から消えたのに memory_status では生きている、という食い違いを明示する。"""
        msg = _format_dismiss_warning("mem_x", 0.0, priority_manager)
        assert "検索結果に出なくなりました" in msg
        assert "memory_status" in msg
        assert 'memory_archive("mem_x")' in msg

    def test_every_dismiss_from_threshold_onward_warns(
        self, priority_manager: PriorityManager
    ):
        """0.5から5回dismissすると、3回目以降は必ず何か言う（黙って消えない）。"""
        p = 0.5
        messages = []
        for _ in range(5):
            p = priority_manager.apply_dismiss(p)
            messages.append(_format_dismiss_warning("mem_x", p, priority_manager))
        assert messages[0] == ""
        assert messages[1] == ""
        assert all(m != "" for m in messages[2:])


class TestArchiveCandidates:
    """受入条件2: archive候補が一覧できる（表示はCYCLE19.6）。"""

    def test_returns_only_low_priority(self, system):
        system.store(content="生きている記憶", layer=3, priority=0.5)
        low = system.store(content="沈んだ記憶", layer=3, priority=0.1)
        candidates = system.archive_candidates()
        assert [m.id for m in candidates] == [low.id]

    def test_threshold_is_inclusive(self, system):
        system.store(content="境界の記憶", layer=3, priority=ARCHIVE_CANDIDATE_THRESHOLD)
        assert len(system.archive_candidates()) == 1

    def test_excludes_already_archived(self, system):
        m = system.store(content="片付け済み", layer=3, priority=0.0)
        system.archive(m.id)
        assert system.archive_candidates() == []

    def test_sorted_by_priority_ascending(self, system):
        mid = system.store(content="中", layer=3, priority=0.2)
        bottom = system.store(content="底", layer=3, priority=0.0)
        low = system.store(content="低", layer=3, priority=0.1)
        assert [m.id for m in system.archive_candidates()] == [bottom.id, low.id, mid.id]

    def test_empty_store_returns_empty(self, system):
        """記憶が1件も無い環境でエラーにならない（新規利用者）。"""
        assert system.archive_candidates() == []

    def test_custom_threshold(self, system):
        system.store(content="やや低い", layer=3, priority=0.3)
        assert system.archive_candidates() == []
        assert len(system.archive_candidates(threshold=0.3)) == 1

    @pytest.mark.asyncio
    async def test_async_matches_sync(self, system):
        system.store(content="沈んだ記憶", layer=3, priority=0.1)
        system.store(content="生きている記憶", layer=3, priority=0.5)
        sync_ids = [m.id for m in system.archive_candidates()]
        async_ids = [m.id for m in await system.async_archive_candidates()]
        assert sync_ids == async_ids
