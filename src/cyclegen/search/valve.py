"""search/valve.py — Personal+Org統合検索バルブ

実装計画書§5.3 / 設計書§3.2 / IP-032:
ローカルMCPサーバーのmemory_searchから呼ばれ、
複数のMemorySourceの検索結果をマージして返す。

フロー:
  1. 各MemorySourceから検索（local=SearchEngine / cloud=OrgClient）
  2. ローカルソースに local_bonus を加算
  3. ソースごとの最低保証枠（source_min_slots）を確保
  4. CognitiveLoadManager（Miller's max_items 件に絞る）
  5. 各結果に source + relevance.reason を付与

CYCLE7.7.2: N-Source対応に汎化（設計書v2 §1.3 Memory Source Resolver）
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from cyclegen.models import Memory, SearchResponse, SearchResult, ValveConfig
from cyclegen.search.cognitive_load import CognitiveLoadManager

if TYPE_CHECKING:
    from cyclegen.org.client import OrgClient
    from cyclegen.search.engine import SearchEngine
    from cyclegen.source.memory_source import MemorySource

logger = logging.getLogger(__name__)


class IntegratedSearchValve:
    """統合検索バルブ（IP-032 MCPバルブ型注入）。

    N個のMemorySourceから検索結果を収集し、
    local_bonusとsource_min_slotsでマージした後、Miller's制限で絞る。
    各ソースの検索失敗時はそのソースをスキップする（オフライン耐性）。
    """

    # --- 新方式（N-Source） ---

    @classmethod
    def from_sources(
        cls,
        sources: list[MemorySource],
        cognitive_load: CognitiveLoadManager,
        valve_config: ValveConfig,
    ) -> IntegratedSearchValve:
        """MemorySourceリストから構築する（Phase3新方式）。"""
        instance = cls.__new__(cls)
        instance._sources = sources
        instance.cognitive_load = cognitive_load
        instance._valve_config = valve_config
        # 旧属性（後方互換: テストや既存コードが直接参照する場合）
        instance.search_engine = None
        instance.org_client = None
        instance.personal_bonus = valve_config.local_bonus
        instance.org_min_slots = valve_config.source_min_slots.get("org", 0)
        return instance

    # --- 旧方式（後方互換） ---

    def __init__(
        self,
        search_engine: SearchEngine,
        cognitive_load: CognitiveLoadManager,
        org_client: OrgClient | None,  # org_server.enabled=false 時は None
        personal_bonus: int = 20,  # PoC検証リファイン対象
        org_min_slots: int = 2,  # FR012: Org結果の最低保証枠
    ):
        self.search_engine = search_engine
        self.cognitive_load = cognitive_load
        self.org_client = org_client
        self.personal_bonus = personal_bonus
        self.org_min_slots = org_min_slots
        # N-Source属性を旧パラメータから構築
        self._sources: list[MemorySource] | None = None
        self._valve_config = ValveConfig(
            local_bonus=personal_bonus,
            source_min_slots={"org": org_min_slots} if org_min_slots > 0 else {},
        )

    def search(
        self,
        query: str,
        personal_memories: list[Memory],
        context: str | None = None,
        layer_filter: list[int] | None = None,
        priority_threshold: float = 0.0,
        max_items: int = 7,
    ) -> SearchResponse:
        """統合検索を実行する。

        N-Sourceモード（from_sources構築）と旧モード（__init__構築）の両方に対応。
        """
        if self._sources is not None:
            return self._search_n_source(
                query, context, layer_filter, priority_threshold, max_items,
            )
        return self._search_legacy(
            query, personal_memories, context, layer_filter, priority_threshold, max_items,
        )

    # --- N-Source検索 ---

    def _search_n_source(
        self,
        query: str,
        context: str | None,
        layer_filter: list[int] | None,
        priority_threshold: float,
        max_items: int,
    ) -> SearchResponse:
        """N-Source方式の検索。各MemorySourceから結果を収集しマージする。"""
        assert self._sources is not None
        start = time.monotonic()
        all_source_results: list[tuple[str, list[SearchResult], bool]] = []
        total_candidates = 0

        # layer_filterの2チャネル分離（CYCLE12.8.3 FR019）
        collect_meta = layer_filter is None or 5 in layer_filter
        if layer_filter is None:
            task_layer_filter = [1, 2, 3, 4]
        else:
            task_layer_filter = [l for l in layer_filter if l != 5] or [1, 2, 3, 4]

        for source in self._sources:
            try:
                if source.client is not None:
                    # cloudバックエンド: OrgClient経由
                    results = source.client.search(
                        query=query, context=context, max_items=max_items * 2,
                    )
                    for r in results:
                        r.source = source.source_label
                    total_candidates += len(results)
                elif source.backend is not None and source.search_engine is not None:
                    # local/postgresqlバックエンド: SearchEngine
                    memories = source.backend.load_all()
                    response = source.search_engine.search(
                        query=query,
                        memories=memories,
                        context=context,
                        layer_filter=task_layer_filter,
                        priority_threshold=priority_threshold,
                        max_items=max_items * 2,
                    )
                    results = response.memories
                    for r in results:
                        r.source = source.source_label
                    total_candidates += response.total_candidates
                else:
                    continue

                all_source_results.append((source.name, results, source.is_local))
            except Exception as e:
                logger.warning(
                    "ソース '%s' 検索失敗（オフライン耐性発動）: %s", source.name, e,
                )

        # マージ
        merged = self._merge_n_source(all_source_results, max_items)

        # Miller's制限
        limited = self.cognitive_load.apply_limit(merged, max_items)

        # メタ認知チャネル（L5）の収集（CYCLE12.8.3 FR019）
        meta_memories = self._collect_meta(
            query, context, priority_threshold,
        ) if collect_meta else []

        elapsed_ms = (time.monotonic() - start) * 1000
        return SearchResponse(
            memories=limited,
            total_candidates=total_candidates,
            search_time_ms=round(elapsed_ms, 2),
            meta_memories=meta_memories,
        )

    def _merge_n_source(
        self,
        source_results: list[tuple[str, list[SearchResult], bool]],
        max_items: int,
    ) -> list[SearchResult]:
        """N-Sourceの結果をマージ。local_bonus加算 + source_min_slots確保。"""
        local_bonus = self._valve_config.local_bonus
        min_slots = self._valve_config.source_min_slots

        guaranteed: list[SearchResult] = []
        rest_pool: list[SearchResult] = []

        for source_name, results, is_local in source_results:
            # ローカルソースにbonus加算
            processed = []
            for r in results:
                if is_local:
                    processed.append(SearchResult(
                        memory=r.memory,
                        score=min(r.score + local_bonus, 100.0),
                        source=r.source,
                        reason=r.reason,
                    ))
                else:
                    processed.append(r)

            # source_min_slotsの確保
            slots = min_slots.get(source_name, 0)
            if slots > 0 and processed:
                sorted_results = sorted(processed, key=lambda x: x.score, reverse=True)
                guaranteed.extend(sorted_results[:slots])
                rest_pool.extend(sorted_results[slots:])
            else:
                rest_pool.extend(processed)

        # 残り枠をスコア順で埋める
        remaining_slots = max_items - len(guaranteed)
        rest_pool.sort(key=lambda r: r.score, reverse=True)
        rest_filled = rest_pool[:remaining_slots] if remaining_slots > 0 else []

        combined = guaranteed + rest_filled
        combined.sort(key=lambda r: r.score, reverse=True)
        return combined

    # --- N-Source非同期検索（CYCLE7.7.3.1追加） ---

    async def async_search(
        self,
        query: str,
        personal_memories: list[Memory] | None = None,
        context: str | None = None,
        layer_filter: list[int] | None = None,
        priority_threshold: float = 0.0,
        max_items: int = 7,
    ) -> SearchResponse:
        """統合検索を実行する（非同期版）。

        N-Sourceモードではbackend.async_load_all()を使用。
        旧モードではpersonal_memoriesを使用（同期）。
        """
        if self._sources is not None:
            return await self._async_search_n_source(
                query, context, layer_filter, priority_threshold, max_items,
            )
        # 旧モードは同期のまま（personal_memoriesが渡される前提）
        return self._search_legacy(
            query, personal_memories or [], context, layer_filter,
            priority_threshold, max_items,
        )

    async def _async_search_n_source(
        self,
        query: str,
        context: str | None,
        layer_filter: list[int] | None,
        priority_threshold: float,
        max_items: int,
    ) -> SearchResponse:
        """N-Source方式の非同期検索。"""
        assert self._sources is not None
        start = time.monotonic()
        all_source_results: list[tuple[str, list[SearchResult], bool]] = []
        total_candidates = 0

        # layer_filterの2チャネル分離（CYCLE12.8.3 FR019）
        collect_meta = layer_filter is None or 5 in layer_filter
        if layer_filter is None:
            task_layer_filter = [1, 2, 3, 4]
        else:
            task_layer_filter = [l for l in layer_filter if l != 5] or [1, 2, 3, 4]

        for source in self._sources:
            try:
                if source.client is not None:
                    # cloudバックエンド: OrgClient経由（同期）
                    results = source.client.search(
                        query=query, context=context, max_items=max_items * 2,
                    )
                    for r in results:
                        r.source = source.source_label
                    total_candidates += len(results)
                elif source.backend is not None and source.search_engine is not None:
                    # local/postgresqlバックエンド: async_load_all + SearchEngine
                    memories = await source.backend.async_load_all()
                    response = source.search_engine.search(
                        query=query,
                        memories=memories,
                        context=context,
                        layer_filter=task_layer_filter,
                        priority_threshold=priority_threshold,
                        max_items=max_items * 2,
                    )
                    results = response.memories
                    for r in results:
                        r.source = source.source_label
                    total_candidates += response.total_candidates
                else:
                    continue

                all_source_results.append((source.name, results, source.is_local))
            except Exception as e:
                logger.warning(
                    "ソース '%s' 検索失敗（オフライン耐性発動）: %s", source.name, e,
                )

        merged = self._merge_n_source(all_source_results, max_items)
        limited = self.cognitive_load.apply_limit(merged, max_items)

        # メタ認知チャネル（L5）の収集（CYCLE12.8.3 FR019）
        meta_memories = await self._async_collect_meta(
            query, context, priority_threshold,
        ) if collect_meta else []

        elapsed_ms = (time.monotonic() - start) * 1000
        return SearchResponse(
            memories=limited,
            total_candidates=total_candidates,
            search_time_ms=round(elapsed_ms, 2),
            meta_memories=meta_memories,
        )

    # --- メタ認知チャネル収集（CYCLE12.8.3 FR019） ---

    def _collect_meta(
        self,
        query: str,
        context: str | None,
        priority_threshold: float,
    ) -> list[SearchResult]:
        """各ソースからL5記憶を収集する（同期）。"""
        assert self._sources is not None
        meta_max = self._valve_config.meta_max_items
        all_meta: list[SearchResult] = []

        for source in self._sources:
            try:
                if source.backend is not None and source.search_engine is not None:
                    memories = source.backend.load_all()
                    response = source.search_engine.search(
                        query=query,
                        memories=memories,
                        context=context,
                        layer_filter=[5],
                        priority_threshold=priority_threshold,
                        max_items=meta_max,
                    )
                    for r in response.memories:
                        r.source = source.source_label
                    all_meta.extend(response.memories)
            except Exception:
                pass  # オフライン耐性

        all_meta.sort(key=lambda r: r.score, reverse=True)
        return all_meta[:meta_max]

    async def _async_collect_meta(
        self,
        query: str,
        context: str | None,
        priority_threshold: float,
    ) -> list[SearchResult]:
        """各ソースからL5記憶を収集する（非同期）。"""
        assert self._sources is not None
        meta_max = self._valve_config.meta_max_items
        all_meta: list[SearchResult] = []

        for source in self._sources:
            try:
                if source.backend is not None and source.search_engine is not None:
                    memories = await source.backend.async_load_all()
                    response = source.search_engine.search(
                        query=query,
                        memories=memories,
                        context=context,
                        layer_filter=[5],
                        priority_threshold=priority_threshold,
                        max_items=meta_max,
                    )
                    for r in response.memories:
                        r.source = source.source_label
                    all_meta.extend(response.memories)
            except Exception:
                pass  # オフライン耐性

        all_meta.sort(key=lambda r: r.score, reverse=True)
        return all_meta[:meta_max]

    # --- 旧方式検索（後方互換） ---

    def _search_legacy(
        self,
        query: str,
        personal_memories: list[Memory],
        context: str | None,
        layer_filter: list[int] | None,
        priority_threshold: float,
        max_items: int,
    ) -> SearchResponse:
        """旧方式の検索（__init__構築時）。"""
        start = time.monotonic()

        # layer_filterの2チャネル分離（CYCLE12.8.3 FR019）
        collect_meta = layer_filter is None or 5 in layer_filter
        if layer_filter is None:
            task_layer_filter = [1, 2, 3, 4]
        else:
            task_layer_filter = [l for l in layer_filter if l != 5] or [1, 2, 3, 4]

        # 1. Personal Layer 検索（タスクチャネル: L1-4）
        personal_response = self.search_engine.search(
            query=query,
            memories=personal_memories,
            context=context,
            layer_filter=task_layer_filter,
            priority_threshold=priority_threshold,
            max_items=max_items * 2,
        )
        personal_results = personal_response.memories
        total_candidates = personal_response.total_candidates

        # 2. Org Layer 検索（オフライン耐性）
        org_results: list[SearchResult] = []
        if self.org_client is not None:
            try:
                org_results = self.org_client.search(
                    query=query,
                    context=context,
                    max_items=max_items * 2,
                )
                total_candidates += len(org_results)
            except Exception as e:
                logger.warning("Org Layer検索失敗（オフライン耐性発動）: %s", e)

        # 3. マージ（Personal +bonus → 統合ソート + Org保証枠）
        merged = self._merge(personal_results, org_results, max_items)

        # 4. CognitiveLoadManager（Miller's 制限）
        limited = self.cognitive_load.apply_limit(merged, max_items)

        # 5. メタ認知チャネル（L5）の収集（CYCLE12.8.3 FR019）
        meta_memories: list[SearchResult] = []
        if collect_meta:
            meta_max = self._valve_config.meta_max_items
            meta_response = self.search_engine.search(
                query=query,
                memories=personal_memories,
                context=context,
                layer_filter=[5],
                priority_threshold=priority_threshold,
                max_items=meta_max,
            )
            meta_memories = meta_response.memories
            for r in meta_memories:
                r.source = "personal"

        elapsed_ms = (time.monotonic() - start) * 1000

        return SearchResponse(
            memories=limited,
            total_candidates=total_candidates,
            search_time_ms=round(elapsed_ms, 2),
            meta_memories=meta_memories,
        )

    def _merge(
        self,
        personal: list[SearchResult],
        org: list[SearchResult],
        max_items: int,
    ) -> list[SearchResult]:
        """Personal結果に personal_bonus を加算してマージ+再ソート。

        org_min_slots: Org結果の最低保証枠（FR012）。
        max_items中のN件をOrg結果に確保し、残り枠をスコア順で埋める。
        """
        boosted = []
        for r in personal:
            boosted.append(SearchResult(
                memory=r.memory,
                score=min(r.score + self.personal_bonus, 100.0),
                source="personal",
                reason=r.reason,
            ))

        # Org結果はそのまま（source="org"はOrgClient/routes側で設定済み）
        if not org or self.org_min_slots <= 0:
            combined = boosted + org
            combined.sort(key=lambda r: r.score, reverse=True)
            return combined

        # FR012: Org最低保証枠
        org_sorted = sorted(org, key=lambda r: r.score, reverse=True)
        guaranteed_org = org_sorted[: self.org_min_slots]
        remaining_org = org_sorted[self.org_min_slots :]

        remaining_slots = max_items - len(guaranteed_org)
        rest_pool = boosted + remaining_org
        rest_pool.sort(key=lambda r: r.score, reverse=True)
        rest_filled = rest_pool[:remaining_slots]

        combined = guaranteed_org + rest_filled
        combined.sort(key=lambda r: r.score, reverse=True)
        return combined
