"""persistence/base.py — 永続化の抽象基底クラス

実装計画書§6.1 / 設計書§4.3: PersistenceAdapterパターン。
SQLite（Personal Layer）とPostgreSQL（Org Layer）の共通インターフェース。

CYCLE7.7.3: async abstract methods追加。
同期版（save/load等）と非同期版（async_save/async_load等）の両方を定義。
デフォルト実装: async版は同期版を呼ぶ（SQLite等のローカルバックエンド向け）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from cyclegen.models import Memory


class PersistenceAdapter(ABC):
    """永続化の抽象基底クラス。

    同期インターフェース（save/load等）と非同期インターフェース（async_save/async_load等）を
    両方定義する。デフォルトでは非同期版が同期版を呼ぶので、同期バックエンド（SQLite等）は
    同期メソッドだけ実装すればよい。非同期バックエンド（asyncpg等）は非同期版をオーバーライドする。
    """

    # === Sync interface（既存） ===

    @abstractmethod
    def save(self, memory: Memory) -> bool:
        """記憶を保存する。"""
        ...

    @abstractmethod
    def load(self, memory_id: str) -> Memory | None:
        """IDで記憶を読み込む。存在しない場合はNone。"""
        ...

    @abstractmethod
    def load_all(self, include_archived: bool = False) -> list[Memory]:
        """全記憶を読み込む。"""
        ...

    @abstractmethod
    def search(
        self,
        keyword: str | None = None,
        layer: int | None = None,
        priority_min: float = 0.0,
        context: str | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        """条件に合致する記憶を検索する。"""
        ...

    @abstractmethod
    def update(self, memory_id: str, updates: dict) -> bool:
        """記憶のフィールドを更新する。"""
        ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """記憶を削除する。"""
        ...

    @abstractmethod
    def count(self, include_archived: bool = False) -> int:
        """記憶の件数を返す。"""
        ...

    # === Async interface（CYCLE7.7.3追加） ===
    # デフォルト実装は同期版を呼ぶ。非同期バックエンドはオーバーライドする。

    async def async_save(self, memory: Memory) -> bool:
        """記憶を保存する（非同期版）。"""
        return self.save(memory)

    async def async_load(self, memory_id: str) -> Memory | None:
        """IDで記憶を読み込む（非同期版）。"""
        return self.load(memory_id)

    async def async_load_all(self, include_archived: bool = False) -> list[Memory]:
        """全記憶を読み込む（非同期版）。"""
        return self.load_all(include_archived)

    async def async_search(
        self,
        keyword: str | None = None,
        layer: int | None = None,
        priority_min: float = 0.0,
        context: str | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        """条件に合致する記憶を検索する（非同期版）。"""
        return self.search(
            keyword=keyword, layer=layer, priority_min=priority_min,
            context=context, limit=limit,
        )

    async def async_update(self, memory_id: str, updates: dict) -> bool:
        """記憶のフィールドを更新する（非同期版）。"""
        return self.update(memory_id, updates)

    async def async_delete(self, memory_id: str) -> bool:
        """記憶を削除する（非同期版）。"""
        return self.delete(memory_id)

    async def async_count(self, include_archived: bool = False) -> int:
        """記憶の件数を返す（非同期版）。"""
        return self.count(include_archived)
