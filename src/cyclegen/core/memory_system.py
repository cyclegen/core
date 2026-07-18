"""core/memory_system.py — 3次元記憶システムのエントリポイント

実装計画書§4.1 / 設計書§1.2:
全コンポーネント（Layer/Priority/Context/Classifier/Persistence/Search）を統合する
中核ファサード。MCPツール群はこのクラスを経由して記憶操作を行う。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from cyclegen.core.classifier import AutoLayerClassifier
from cyclegen.core.context import ContextSelector
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.priority import CURRENT_SCORE_VERSION, EventCounts, PriorityManager
from cyclegen.models import Coordinates, Memory, SearchResponse
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
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # CYCLE12.7.4: embedding自動生成
        embedding = None
        if self._embedding_manager:
            embedding = self._embedding_manager.embed(content)

        memory = Memory(
            content=content,
            coordinates=coordinates,
            tags=tags or [],
            owner_id=owner_id,
            agent_id=agent_id,
            content_hash=content_hash,
            embedding=embedding,
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

    def update(self, memory_id: str, updates: dict) -> Memory | None:
        """記憶のフィールドを更新する。version楽観的ロック。"""
        success = self.persistence.update(memory_id, updates)
        if not success:
            return None
        return self.persistence.load(memory_id)

    def delete(self, memory_id: str) -> bool:
        """記憶を削除する。"""
        return self.persistence.delete(memory_id)

    def pin(self, memory_id: str) -> Memory | None:
        """pinned=True にする。Priority減衰を停止。"""
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
        """Priority +0.15、access_count++。上限1.0。"""
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
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # CYCLE12.7.4: embedding自動生成
        embedding = None
        if self._embedding_manager:
            embedding = self._embedding_manager.embed(content)

        memory = Memory(
            content=content,
            coordinates=coordinates,
            tags=tags or [],
            owner_id=owner_id,
            agent_id=agent_id,
            content_hash=content_hash,
            embedding=embedding,
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
        """記憶のフィールドを更新する（非同期版）。"""
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
        """Priority +0.15、access_count++（非同期版）。"""
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
