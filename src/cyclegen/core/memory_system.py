"""core/memory_system.py — 3次元記憶システムのエントリポイント

実装計画書§4.1 / 設計書§1.2:
全コンポーネント（Layer/Priority/Context/Classifier/Persistence/Search）を統合する
中核ファサード。MCPツール群はこのクラスを経由して記憶操作を行う。
"""

from __future__ import annotations

import logging
from datetime import datetime

from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.priority import (
    ARCHIVE_CANDIDATE_THRESHOLD,
    CURRENT_SCORE_VERSION,
    EventCounts,
    PriorityManager,
)
from cyclegen.models import Coordinates, Memory, SearchResponse, compute_content_hash
from cyclegen.persistence.base import PersistenceAdapter
from cyclegen.search.engine import SearchEngine

if TYPE_CHECKING:
    from cyclegen.search.context_detector import ContextAutoDetector
    from cyclegen.search.embedding import EmbeddingManager


class MemorySystem3D:
    """3次元記憶の保存・取得・更新・削除を統合管理する。

    設計書§1.2 設計原則:
    - 全記憶を Layer(1-5) × Priority(0.0-1.0) × Context(7+種類) で管理
    - PersistenceAdapterを介して永続化を抽象化
    """

    def __init__(
        self,
        persistence: PersistenceAdapter,
        layer_hierarchy: LayerHierarchy,
        priority_manager: PriorityManager,
        context_selector: ContextSelector,
        classifier: AutoLayerClassifier,
        search_engine: SearchEngine | None = None,
        embedding_manager: "EmbeddingManager | None" = None,
        context_detector: "ContextAutoDetector | None" = None,
    ):
        self.persistence = persistence
        self.layer_hierarchy = layer_hierarchy
        self.priority_manager = priority_manager
        self.context_selector = context_selector
        self.classifier = classifier
        self.search_engine = search_engine or SearchEngine()
        self._embedding_manager = embedding_manager
        self._context_detector = context_detector

    def store(
        self,
        content: str,
        layer: int | None = None,
        priority: float | None = None,
        context: str | None = None,
        tags: list[str] | None = None,
        owner_id: str = "",
        agent_id: str | None = None,
    ) -> Memory:
        """記憶を保存する。

        layer省略時: AutoLayerClassifierが自動判定
        priority省略時: PriorityManagerが内容から推定
        context省略時: ContextSelectorが自動検出

        Returns: 保存された Memory オブジェクト
        """
        # CYCLE12.8.2 FR023: 未定義Contextバリデーション
        if context is not None and not self.context_selector.validate(context):
            logger.warning("未定義Context '%s' → 自動判定に切り替え", context)
            context = None  # 自動判定に委ねる

        # 自動判定: embedding類似度 → キーワードベース の優先順
        if context is None:
            if self._context_detector is not None:
                context = self._context_detector.detect(content)
            if context is None:
                context = self.context_selector.detect(content)
        if layer is None:
            layer = self.classifier.classify(content, context)
        if priority is None:
            priority = self.priority_manager.estimate_initial(content)

        # バリデーション
        if not self.layer_hierarchy.validate(layer):
            raise ValueError(f"Layer must be 1-5, got {layer}")

        coordinates = Coordinates(layer=layer, priority=priority, context=context)
        content_hash = compute_content_hash(content)

        # CYCLE12.7.4: embedding自動生成
        # CYCLE19.2 (A8): どのモデルで作ったかを一緒に記録する。
        # embeddingとmodel_idは必ず同時に決まる（片方だけ入るとNULLの意味が濁る）。
        embedding = None
        embedding_model = None
        if self._embedding_manager:
            embedding = self._embedding_manager.embed(content)
            embedding_model = self._embedding_manager.model_id

        memory = Memory(
            content=content,
            coordinates=coordinates,
            tags=tags or [],
            owner_id=owner_id,
            agent_id=agent_id,
            content_hash=content_hash,
            embedding=embedding,
            embedding_model=embedding_model,
            score_version=CURRENT_SCORE_VERSION,
        )

        self.persistence.save(memory)
        return memory

    def find_by_hash(self, content_hash: str) -> Memory | None:
        """content_hashで既存の記憶を検索する（重複検知用）。"""
        for memory in self.persistence.load_all():
            if memory.content_hash == content_hash:
                return memory
        return None

    def search(
        self,
        query: str,
        context: str | None = None,
        layer_filter: list[int] | None = None,
        priority_threshold: float = 0.0,
        max_items: int = 7,
    ) -> SearchResponse:
        """3段パイプラインで検索する（Personal Layer のみ）。

        ※ Personal+Org統合検索は search/valve.py が担当
        """
        memories = self.persistence.load_all()
        return self.search_engine.search(
            query=query,
            memories=memories,
            context=context,
            layer_filter=layer_filter,
            priority_threshold=priority_threshold,
            max_items=max_items,
        )

    def _with_refreshed_embedding(self, updates: dict) -> dict:
        """contentが変わる更新なら、embeddingを作り直して updates に足す。

        CYCLE19.1（A7）: store() はembeddingを生成するのに update() は
        生成しておらず、内容を書き換えるとembeddingだけ古い内容のまま残っていた。
        母艦2,067件のうち6件（0.3%）で実際にズレを確認（自己類似度 最低0.67）。
        embeddingが古いままでも例外は出ず、その記憶が検索で当たらなくなるだけなので
        利用者は気づけない。

        呼び出し側が embedding を明示指定している場合（memory_reembed 等）は
        そちらを尊重して何もしない。
        """
        if "content" not in updates:
            return updates
        if "embedding" in updates:
            return updates
        if self._embedding_manager is None:
            return updates
        # CYCLE19.2 (A8): embeddingを作り直したら出所も更新する。
        # ここを忘れると「新しいembedding × 古いモデル名」という、
        # 記録があるのに嘘という最悪の状態ができる。
        return {
            **updates,
            "embedding": self._embedding_manager.embed(updates["content"]),
            "embedding_model": self._embedding_manager.model_id,
        }

    def update(self, memory_id: str, updates: dict) -> Memory | None:
        """記憶のフィールドを更新する。version楽観的ロック。

        content変更時はembeddingを再生成する（CYCLE19.1 / A7）。
        """
        updates = self._with_refreshed_embedding(updates)
        success = self.persistence.update(memory_id, updates)
        if not success:
            return None
        return self.persistence.load(memory_id)

    def delete(self, memory_id: str) -> bool:
        """記憶を削除する。"""
        return self.persistence.delete(memory_id)

    def pin(self, memory_id: str) -> Memory | None:
        """pinned=True にする（重要マーク）。

        CYCLE19.1: かつて「鮮度減衰を止める」と説明していたが、鮮度減衰は
        CYCLE12.7.4で廃止済みで実装に存在せず、止める対象が無かった。
        pinnedの実効果は検索結果での📌表示と、昇格の無条件候補化（Enterpriseのみ）。
        """
        success = self.persistence.update(memory_id, {"pinned": True})
        if not success:
            return None
        return self.persistence.load(memory_id)

    def archive(self, memory_id: str) -> Memory | None:
        """archived=True にする。通常検索から除外。"""
        success = self.persistence.update(memory_id, {"archived": True})
        if not success:
            return None
        return self.persistence.load(memory_id)

    def unarchive(self, memory_id: str) -> Memory | None:
        """archived=False にする。通常検索に復帰。"""
        success = self.persistence.update(memory_id, {"archived": False})
        if not success:
            return None
        return self.persistence.load(memory_id)

    def boost(self, memory_id: str) -> Memory | None:
        """Priority +0.10、access_count++。上限1.0。"""
        memory = self.persistence.load(memory_id)
        if memory is None:
            return None

        new_priority = self.priority_manager.apply_boost(memory.coordinates.priority)
        self.persistence.update(memory_id, {
            "coordinates.priority": new_priority,
            "access_count": memory.access_count + 1,
            "last_accessed_at": datetime.now(),
        })
        return self.persistence.load(memory_id)

    def dismiss(self, memory_id: str) -> Memory | None:
        """Priority -0.10 + last_accessed_at 更新。下限0.0。"""
        memory = self.persistence.load(memory_id)
        if memory is None:
            return None

        new_priority = self.priority_manager.apply_dismiss(memory.coordinates.priority)
        self.persistence.update(memory_id, {
            "coordinates.priority": new_priority,
            "last_accessed_at": datetime.now(),
        })
        return self.persistence.load(memory_id)

    @staticmethod
    def _select_archive_candidates(
        memories: list[Memory], threshold: float | None
    ) -> list[Memory]:
        if threshold is None:
            threshold = ARCHIVE_CANDIDATE_THRESHOLD
        candidates = [
            m for m in memories
            if not m.archived and m.coordinates.priority <= threshold
        ]
        return sorted(candidates, key=lambda m: m.coordinates.priority)

    def archive_candidates(
        self, threshold: float | None = None, memories: list[Memory] | None = None
    ) -> list[Memory]:
        """archive候補（Priorityが閾値まで落ちた未archiveの記憶）を返す。

        CYCLE19.4（A5-3）: 「検索から消えているのに生きていると数えられている記憶」を
        利用者が一覧できるようにする経路。archiveするかどうかは人が決める。
        表示（memory_diagnostics）はCYCLE19.6。

        Priorityの低い順（＝消えるのに近い順）に並べて返す。

        Args:
            memories: 読み込み済みの記憶。診断のように複数の集計を続けて回す場面で、
                同じ `load_all` を何度も走らせないために渡す（CYCLE19.6）。
        """
        if memories is None:
            memories = self.persistence.load_all(include_archived=False)
        return self._select_archive_candidates(memories, threshold)

    async def async_archive_candidates(
        self, threshold: float | None = None, memories: list[Memory] | None = None
    ) -> list[Memory]:
        """archive候補を返す（非同期版）。CYCLE19.4。"""
        if memories is None:
            memories = await self.persistence.async_load_all(include_archived=False)
        return self._select_archive_candidates(memories, threshold)

    def record_access(self, memory_id: str) -> None:
        """access_count++ と last_accessed_at 更新。

        CYCLE12.7.4: Priority増進を廃止（正のフィードバックループ解消）。
        """
        memory = self.persistence.load(memory_id)
        if memory is None:
            return

        self.persistence.update(memory_id, {
            "access_count": memory.access_count + 1,
            "last_accessed_at": datetime.now(),
        })

    # === Async interface（CYCLE7.7.3追加） ===
    # PersistenceAdapterのasyncメソッドを呼ぶ。
    # 同期バックエンド（SQLite）はデフォルト実装で同期版を呼ぶ。
    # 非同期バックエンド（asyncpg）はオーバーライドされたasyncメソッドを呼ぶ。

    async def async_store(
        self,
        content: str,
        layer: int | None = None,
        priority: float | None = None,
        context: str | None = None,
        tags: list[str] | None = None,
        owner_id: str = "",
        agent_id: str | None = None,
    ) -> Memory:
        """記憶を保存する（非同期版）。"""
        # CYCLE12.8.2 FR023: 未定義Contextバリデーション
        if context is not None and not self.context_selector.validate(context):
            logger.warning("未定義Context '%s' → 自動判定に切り替え", context)
            context = None  # 自動判定に委ねる

        # 自動判定: embedding類似度 → キーワードベース の優先順
        if context is None:
            if self._context_detector is not None:
                context = self._context_detector.detect(content)
            if context is None:
                context = self.context_selector.detect(content)
        if layer is None:
            layer = self.classifier.classify(content, context)
        if priority is None:
            priority = self.priority_manager.estimate_initial(content)

        if not self.layer_hierarchy.validate(layer):
            raise ValueError(f"Layer must be 1-5, got {layer}")

        coordinates = Coordinates(layer=layer, priority=priority, context=context)
        content_hash = compute_content_hash(content)

        # CYCLE12.7.4: embedding自動生成
        # CYCLE19.2 (A8): どのモデルで作ったかを一緒に記録する。
        # embeddingとmodel_idは必ず同時に決まる（片方だけ入るとNULLの意味が濁る）。
        embedding = None
        embedding_model = None
        if self._embedding_manager:
            embedding = self._embedding_manager.embed(content)
            embedding_model = self._embedding_manager.model_id

        memory = Memory(
            content=content,
            coordinates=coordinates,
            tags=tags or [],
            owner_id=owner_id,
            agent_id=agent_id,
            content_hash=content_hash,
            embedding=embedding,
            embedding_model=embedding_model,
            score_version=CURRENT_SCORE_VERSION,
        )

        await self.persistence.async_save(memory)
        return memory

    async def async_find_by_hash(self, content_hash: str) -> Memory | None:
        """content_hashで既存の記憶を検索する（非同期版）。"""
        for memory in await self.persistence.async_load_all():
            if memory.content_hash == content_hash:
                return memory
        return None

    async def async_search(
        self,
        query: str,
        context: str | None = None,
        layer_filter: list[int] | None = None,
        priority_threshold: float = 0.0,
        max_items: int = 7,
    ) -> SearchResponse:
        """3段パイプラインで検索する（非同期版）。"""
        memories = await self.persistence.async_load_all()
        return self.search_engine.search(
            query=query,
            memories=memories,
            context=context,
            layer_filter=layer_filter,
            priority_threshold=priority_threshold,
            max_items=max_items,
        )

    async def async_update(self, memory_id: str, updates: dict) -> Memory | None:
        """記憶のフィールドを更新する（非同期版）。

        content変更時はembeddingを再生成する（CYCLE19.1 / A7）。
        """
        updates = self._with_refreshed_embedding(updates)
        success = await self.persistence.async_update(memory_id, updates)
        if not success:
            return None
        return await self.persistence.async_load(memory_id)

    async def async_delete(self, memory_id: str) -> bool:
        """記憶を削除する（非同期版）。"""
        return await self.persistence.async_delete(memory_id)

    async def async_pin(self, memory_id: str) -> Memory | None:
        """pinned=True にする（非同期版）。"""
        success = await self.persistence.async_update(memory_id, {"pinned": True})
        if not success:
            return None
        return await self.persistence.async_load(memory_id)

    async def async_archive(self, memory_id: str) -> Memory | None:
        """archived=True にする（非同期版）。"""
        success = await self.persistence.async_update(memory_id, {"archived": True})
        if not success:
            return None
        return await self.persistence.async_load(memory_id)

    async def async_unarchive(self, memory_id: str) -> Memory | None:
        """archived=False にする（非同期版）。"""
        success = await self.persistence.async_update(memory_id, {"archived": False})
        if not success:
            return None
        return await self.persistence.async_load(memory_id)

    async def async_boost(self, memory_id: str) -> Memory | None:
        """Priority +0.10、access_count++（非同期版）。"""
        memory = await self.persistence.async_load(memory_id)
        if memory is None:
            return None

        new_priority = self.priority_manager.apply_boost(memory.coordinates.priority)
        await self.persistence.async_update(memory_id, {
            "coordinates.priority": new_priority,
            "access_count": memory.access_count + 1,
            "last_accessed_at": datetime.now(),
        })
        return await self.persistence.async_load(memory_id)

    async def async_dismiss(self, memory_id: str) -> Memory | None:
        """Priority -0.10 + last_accessed_at 更新（非同期版）。"""
        memory = await self.persistence.async_load(memory_id)
        if memory is None:
            return None

        new_priority = self.priority_manager.apply_dismiss(memory.coordinates.priority)
        await self.persistence.async_update(memory_id, {
            "coordinates.priority": new_priority,
            "last_accessed_at": datetime.now(),
        })
        return await self.persistence.async_load(memory_id)

    async def async_record_access(self, memory_id: str) -> None:
        """access_count++ と last_accessed_at 更新（非同期版）。

        CYCLE12.7.4: Priority増進を廃止（正のフィードバックループ解消）。
        """
        memory = await self.persistence.async_load(memory_id)
        if memory is None:
            return

        await self.persistence.async_update(memory_id, {
            "access_count": memory.access_count + 1,
            "last_accessed_at": datetime.now(),
        })
