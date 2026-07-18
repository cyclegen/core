"""source/resolver.py — Memory Source Resolver

設計書v2 §1.3: 宣言的設定（cyclegen_config.yaml の memory_sources）から
具体的な MemorySource リストを解決する。

memory_sources セクションが未定義の場合は旧設定（環境変数方式）にフォールバックする。

CYCLE7.7.3: async_resolve追加。PostgreSQLバックエンドをasyncpg版に対応。
"""

from __future__ import annotations

import logging
import os

from cyclegen.models import CycleGenConfig, MemorySourceConfig
from cyclegen.search.engine import SearchEngine
from cyclegen.source.memory_source import MemorySource

logger = logging.getLogger(__name__)


class MemorySourceResolver:
    """宣言的設定 → MemorySourceリストの解決。"""

    def resolve(self, config: CycleGenConfig) -> list[MemorySource]:
        """CycleGenConfigからMemorySourceリストを生成する（同期版）。

        memory_sourcesセクションがあれば新方式、なければ旧方式にフォールバック。
        PostgreSQLバックエンドはSyncPostgreSQLPersistence（psycopg2）を使用。
        """
        if config.memory_sources:
            return self._resolve_new(config)
        return self._resolve_legacy(config)

    async def async_resolve(self, config: CycleGenConfig) -> list[MemorySource]:
        """CycleGenConfigからMemorySourceリストを生成する（非同期版）。

        PostgreSQLバックエンドをAsyncPostgreSQLPersistence（asyncpg）で解決する。
        local/cloudバックエンドは同期版と同じ。
        """
        if config.memory_sources:
            return await self._async_resolve_new(config)
        return await self._async_resolve_legacy(config)

    def _resolve_new(self, config: CycleGenConfig) -> list[MemorySource]:
        """新方式: memory_sourcesセクションからMemorySourceリストを生成。"""
        sources: list[MemorySource] = []

        for src_cfg in config.memory_sources:
            # 環境変数展開（${VAR:default} 形式）
            owner_id = self._expand_env(src_cfg.owner_id) if src_cfg.owner_id else None

            if src_cfg.backend == "local":
                source = self._create_local_source(src_cfg, config, owner_id)
            elif src_cfg.backend == "postgresql":
                source = self._create_postgresql_source(src_cfg, config, owner_id)
            elif src_cfg.backend == "cloud":
                source = self._create_cloud_source(src_cfg, config, owner_id)
            else:
                logger.warning("不明なbackend種別: %s（スキップ）", src_cfg.backend)
                continue

            sources.append(source)
            logger.info(
                "MemorySource解決: %s (backend=%s, is_local=%s)",
                source.name, src_cfg.backend, source.is_local,
            )

        return sources

    def _resolve_legacy(self, config: CycleGenConfig) -> list[MemorySource]:
        """旧方式: 環境変数 + CycleGenConfig からMemorySourceリストを生成。

        後方互換: memory_sourcesセクションが未定義の場合のフォールバック。
        """
        sources: list[MemorySource] = []
        persistence_type = os.environ.get("CYCLEGEN_PERSISTENCE", "sqlite")

        if persistence_type == "postgresql":
            # Cloud上のPostgreSQLをPersonalとして使用（パターン3/4）
            dsn = os.environ.get("CYCLEGEN_DATABASE_URL", "")
            table = os.environ.get("CYCLEGEN_TABLE", "org_memories")
            src_cfg = MemorySourceConfig(
                name="personal",
                backend="postgresql",
                dsn=dsn,
                table=table,
            )
            sources.append(self._create_postgresql_source(src_cfg, config, None))
        else:
            # ローカルSQLite+md（パターン1）
            src_cfg = MemorySourceConfig(name="personal", backend="local")
            sources.append(self._create_local_source(src_cfg, config, None))

        # Org Layer（旧設定: org_server_enabled）
        if config.org_server_enabled:
            src_cfg = MemorySourceConfig(
                name="org",
                backend="cloud",
                url=config.org_server_url,
                api_key=config.org_api_key,
            )
            sources.append(self._create_cloud_source(src_cfg, config, None))

        logger.info("レガシー設定フォールバック: %d ソース解決", len(sources))
        return sources

    def _create_local_source(
        self, src_cfg: MemorySourceConfig, config: CycleGenConfig, owner_id: str | None,
    ) -> MemorySource:
        """localバックエンド: MdWithSQLitePersistence"""
        from cyclegen.config import load_contexts, resolve_home
        from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence

        home = resolve_home(config)
        persistence = MdWithSQLitePersistence(home)
        persistence.sync_from_md()

        return MemorySource(
            name=src_cfg.name,
            backend=persistence,
            search_engine=SearchEngine(weights=config.scoring_weights),
            owner_id=owner_id,
            source_label=src_cfg.name,
            is_local=True,
        )

    def _create_postgresql_source(
        self, src_cfg: MemorySourceConfig, config: CycleGenConfig, owner_id: str | None,
    ) -> MemorySource:
        """postgresqlバックエンド: SyncPostgreSQLPersistence"""
        from cyclegen.persistence.postgresql_sync import SyncPostgreSQLPersistence

        dsn = self._expand_env(src_cfg.dsn) if src_cfg.dsn else os.environ.get("CYCLEGEN_DATABASE_URL", "")
        table = src_cfg.table or os.environ.get("CYCLEGEN_TABLE", "org_memories")

        persistence = SyncPostgreSQLPersistence(dsn=dsn, table_name=table)
        persistence.init_tables()

        return MemorySource(
            name=src_cfg.name,
            backend=persistence,
            search_engine=SearchEngine(weights=config.scoring_weights),
            owner_id=owner_id,
            source_label=src_cfg.name,
            is_local=False,
        )

    def _create_cloud_source(
        self, src_cfg: MemorySourceConfig, config: CycleGenConfig, owner_id: str | None,
    ) -> MemorySource:
        """cloudバックエンド: OrgClient（REST API経由）"""
        from cyclegen.org.client import OrgClient

        # 新設定のurl/api_keyを優先、なければ旧設定にフォールバック
        url = self._expand_env(src_cfg.url) if src_cfg.url else config.org_server_url
        api_key = self._expand_env(src_cfg.api_key) if src_cfg.api_key else config.org_api_key

        # OrgClientは現在CycleGenConfigを受け取るが、url/api_keyだけ上書き
        client_config = config.model_copy(update={
            "org_server_url": url,
            "org_api_key": api_key,
        })
        client = OrgClient(client_config)

        return MemorySource(
            name=src_cfg.name,
            client=client,
            owner_id=owner_id,
            source_label=src_cfg.name,
            is_local=False,
        )

    # === Async methods（CYCLE7.7.3追加） ===

    async def _async_resolve_new(self, config: CycleGenConfig) -> list[MemorySource]:
        """新方式の非同期版。"""
        sources: list[MemorySource] = []

        for src_cfg in config.memory_sources:
            owner_id = self._expand_env(src_cfg.owner_id) if src_cfg.owner_id else None

            if src_cfg.backend == "local":
                source = self._create_local_source(src_cfg, config, owner_id)
            elif src_cfg.backend == "postgresql":
                source = await self._async_create_postgresql_source(src_cfg, config, owner_id)
            elif src_cfg.backend == "cloud":
                source = self._create_cloud_source(src_cfg, config, owner_id)
            else:
                logger.warning("不明なbackend種別: %s（スキップ）", src_cfg.backend)
                continue

            sources.append(source)
            logger.info(
                "MemorySource解決(async): %s (backend=%s, is_local=%s)",
                source.name, src_cfg.backend, source.is_local,
            )

        return sources

    async def _async_resolve_legacy(self, config: CycleGenConfig) -> list[MemorySource]:
        """旧方式の非同期版。"""
        sources: list[MemorySource] = []
        persistence_type = os.environ.get("CYCLEGEN_PERSISTENCE", "sqlite")

        if persistence_type == "postgresql":
            dsn = os.environ.get("CYCLEGEN_DATABASE_URL", "")
            table = os.environ.get("CYCLEGEN_TABLE", "memories")
            src_cfg = MemorySourceConfig(
                name="personal",
                backend="postgresql",
                dsn=dsn,
                table=table,
            )
            sources.append(await self._async_create_postgresql_source(src_cfg, config, None))
        else:
            src_cfg = MemorySourceConfig(name="personal", backend="local")
            sources.append(self._create_local_source(src_cfg, config, None))

        if config.org_server_enabled:
            src_cfg = MemorySourceConfig(
                name="org",
                backend="cloud",
                url=config.org_server_url,
                api_key=config.org_api_key,
            )
            sources.append(self._create_cloud_source(src_cfg, config, None))

        logger.info("レガシー設定フォールバック(async): %d ソース解決", len(sources))
        return sources

    async def _async_create_postgresql_source(
        self, src_cfg: MemorySourceConfig, config: CycleGenConfig, owner_id: str | None,
    ) -> MemorySource:
        """postgresqlバックエンド: AsyncPostgreSQLPersistence（asyncpg）"""
        from cyclegen.persistence.postgresql_async import AsyncPostgreSQLPersistence

        dsn = self._expand_env(src_cfg.dsn) if src_cfg.dsn else os.environ.get("CYCLEGEN_DATABASE_URL", "")
        table = src_cfg.table or os.environ.get("CYCLEGEN_TABLE", "memories")

        persistence = AsyncPostgreSQLPersistence(dsn=dsn, table_name=table)
        await persistence.init_pool()
        await persistence.init_tables()

        return MemorySource(
            name=src_cfg.name,
            backend=persistence,
            search_engine=SearchEngine(weights=config.scoring_weights),
            owner_id=owner_id,
            source_label=src_cfg.name,
            is_local=False,
        )

    @staticmethod
    def _expand_env(value: str) -> str:
        """${VAR} または ${VAR:default} 形式の環境変数を展開する。"""
        if not value or not value.startswith("${"):
            return value

        # ${VAR:default} → VAR, default
        inner = value[2:-1] if value.endswith("}") else value[2:]
        if ":" in inner:
            var_name, default = inner.split(":", 1)
        else:
            var_name, default = inner, ""

        return os.environ.get(var_name, default)
