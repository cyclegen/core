"""test_resolver.py — MemorySourceResolver のユニットテスト

Memory Source Resolver（設計書v2 §1.3）の動作を検証:
- 新方式: memory_sourcesセクションからの解決
- 旧方式: 環境変数フォールバック
- 環境変数展開（${VAR:default}）
- バックエンド種別の解決
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cyclegen.models import (
    CycleGenConfig,
    MemorySourceConfig,
    ScoringWeights,
    ValveConfig,
)
from cyclegen.source.memory_source import MemorySource
from cyclegen.source.resolver import MemorySourceResolver


class TestExpandEnv:
    """環境変数展開のテスト"""

    def test_plain_value(self):
        assert MemorySourceResolver._expand_env("hello") == "hello"

    def test_empty_string(self):
        assert MemorySourceResolver._expand_env("") == ""

    def test_env_var_with_default(self):
        result = MemorySourceResolver._expand_env("${NONEXISTENT_VAR:fallback}")
        assert result == "fallback"

    def test_env_var_without_default(self):
        result = MemorySourceResolver._expand_env("${NONEXISTENT_VAR}")
        assert result == ""

    def test_env_var_set(self):
        with patch.dict("os.environ", {"MY_TEST_VAR": "actual_value"}):
            result = MemorySourceResolver._expand_env("${MY_TEST_VAR:default}")
            assert result == "actual_value"

    def test_env_var_set_no_default(self):
        with patch.dict("os.environ", {"MY_TEST_VAR": "val"}):
            result = MemorySourceResolver._expand_env("${MY_TEST_VAR}")
            assert result == "val"


class TestResolveLegacy:
    """旧方式フォールバックのテスト"""

    def test_sqlite_default(self):
        """CYCLEGEN_PERSISTENCE未設定 → ローカルSQLiteソース1つ"""
        config = CycleGenConfig()
        resolver = MemorySourceResolver()

        with patch.dict("os.environ", {}, clear=False):
            # sqlite モードのため md_sqlite が必要 → モック
            with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local:
                mock_source = MemorySource(name="personal", is_local=True)
                mock_local.return_value = mock_source

                sources = resolver._resolve_legacy(config)

        assert len(sources) == 1
        assert sources[0].name == "personal"
        assert sources[0].is_local is True

    def test_sqlite_with_org(self):
        """ローカル + Org有効 → 2ソース"""
        config = CycleGenConfig(
            org_server_enabled=True,
            org_server_url="https://org.example.com",
            org_api_key="key123",
        )
        resolver = MemorySourceResolver()

        with patch.dict("os.environ", {}, clear=False):
            with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local, \
                 patch("cyclegen.source.resolver.MemorySourceResolver._create_cloud_source") as mock_cloud:
                mock_local.return_value = MemorySource(name="personal", is_local=True)
                mock_cloud.return_value = MemorySource(name="org", is_local=False)

                sources = resolver._resolve_legacy(config)

        assert len(sources) == 2
        assert sources[0].name == "personal"
        assert sources[1].name == "org"

    def test_postgresql_mode(self):
        """CYCLEGEN_PERSISTENCE=postgresql → PostgreSQLソース"""
        config = CycleGenConfig()
        resolver = MemorySourceResolver()

        with patch.dict("os.environ", {"CYCLEGEN_PERSISTENCE": "postgresql", "CYCLEGEN_DATABASE_URL": "postgresql://test"}):
            with patch("cyclegen.source.resolver.MemorySourceResolver._create_postgresql_source") as mock_pg:
                mock_pg.return_value = MemorySource(name="personal", is_local=False)

                sources = resolver._resolve_legacy(config)

        assert len(sources) == 1
        assert sources[0].name == "personal"
        assert sources[0].is_local is False


class TestResolveNew:
    """新方式（memory_sourcesセクション）のテスト"""

    def test_local_source(self):
        """localバックエンドの解決"""
        config = CycleGenConfig(
            memory_sources=[MemorySourceConfig(name="personal", backend="local")],
        )
        resolver = MemorySourceResolver()

        with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local:
            mock_local.return_value = MemorySource(name="personal", is_local=True)
            sources = resolver.resolve(config)

        assert len(sources) == 1
        assert sources[0].name == "personal"

    def test_cloud_source(self):
        """cloudバックエンドの解決"""
        config = CycleGenConfig(
            memory_sources=[
                MemorySourceConfig(name="org", backend="cloud", url="https://org.example.com"),
            ],
        )
        resolver = MemorySourceResolver()

        with patch("cyclegen.source.resolver.MemorySourceResolver._create_cloud_source") as mock_cloud:
            mock_cloud.return_value = MemorySource(name="org", is_local=False)
            sources = resolver.resolve(config)

        assert len(sources) == 1
        assert sources[0].name == "org"

    def test_multi_source(self):
        """複数ソースの解決"""
        config = CycleGenConfig(
            memory_sources=[
                MemorySourceConfig(name="personal", backend="local"),
                MemorySourceConfig(name="org", backend="cloud", url="https://org.example.com"),
            ],
        )
        resolver = MemorySourceResolver()

        with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local, \
             patch("cyclegen.source.resolver.MemorySourceResolver._create_cloud_source") as mock_cloud:
            mock_local.return_value = MemorySource(name="personal", is_local=True)
            mock_cloud.return_value = MemorySource(name="org", is_local=False)
            sources = resolver.resolve(config)

        assert len(sources) == 2
        assert sources[0].name == "personal"
        assert sources[1].name == "org"

    def test_unknown_backend_skipped(self):
        """不明なbackendはスキップ"""
        config = CycleGenConfig(
            memory_sources=[
                MemorySourceConfig(name="weird", backend="unknown"),
            ],
        )
        resolver = MemorySourceResolver()
        sources = resolver.resolve(config)
        assert len(sources) == 0

    def test_owner_id_env_expansion(self):
        """owner_idの環境変数展開"""
        config = CycleGenConfig(
            memory_sources=[
                MemorySourceConfig(name="personal", backend="local", owner_id="${TEST_OWNER:default_user}"),
            ],
        )
        resolver = MemorySourceResolver()

        with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local:
            mock_local.return_value = MemorySource(name="personal", is_local=True, owner_id="default_user")
            sources = resolver.resolve(config)

            # _create_local_sourceの第3引数（owner_id）に展開後の値が渡される
            call_args = mock_local.call_args[0]  # positional args
            # (src_cfg, config, owner_id)
            assert call_args[2] == "default_user"

    def test_fallback_to_legacy(self):
        """memory_sources未定義 → 旧方式フォールバック"""
        config = CycleGenConfig()  # memory_sources=[]
        resolver = MemorySourceResolver()

        with patch.object(resolver, "_resolve_legacy") as mock_legacy:
            mock_legacy.return_value = [MemorySource(name="personal", is_local=True)]
            sources = resolver.resolve(config)

        mock_legacy.assert_called_once()
        assert len(sources) == 1


class TestAsyncResolve:
    """async_resolveのテスト（CYCLE7.7.3）"""

    @pytest.mark.asyncio
    async def test_async_resolve_local_only(self):
        """ローカルのみ → async_resolveでも同期ローカルソースを返す"""
        config = CycleGenConfig(
            memory_sources=[MemorySourceConfig(name="personal", backend="local")],
        )
        resolver = MemorySourceResolver()

        with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local:
            mock_local.return_value = MemorySource(name="personal", is_local=True)
            sources = await resolver.async_resolve(config)

        assert len(sources) == 1
        assert sources[0].name == "personal"
        assert sources[0].is_local is True

    @pytest.mark.asyncio
    async def test_async_resolve_postgresql(self):
        """postgresqlバックエンド → _async_create_postgresql_sourceが呼ばれる"""
        config = CycleGenConfig(
            memory_sources=[
                MemorySourceConfig(name="personal", backend="postgresql", dsn="postgresql://test"),
            ],
        )
        resolver = MemorySourceResolver()

        with patch.object(resolver, "_async_create_postgresql_source") as mock_pg:
            mock_pg.return_value = MemorySource(name="personal", is_local=False)
            sources = await resolver.async_resolve(config)

        mock_pg.assert_called_once()
        assert len(sources) == 1
        assert sources[0].is_local is False

    @pytest.mark.asyncio
    async def test_async_resolve_mixed(self):
        """local + cloud → local同期 + cloud同期"""
        config = CycleGenConfig(
            memory_sources=[
                MemorySourceConfig(name="personal", backend="local"),
                MemorySourceConfig(name="org", backend="cloud", url="https://org.example.com"),
            ],
        )
        resolver = MemorySourceResolver()

        with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local, \
             patch("cyclegen.source.resolver.MemorySourceResolver._create_cloud_source") as mock_cloud:
            mock_local.return_value = MemorySource(name="personal", is_local=True)
            mock_cloud.return_value = MemorySource(name="org", is_local=False)
            sources = await resolver.async_resolve(config)

        assert len(sources) == 2

    @pytest.mark.asyncio
    async def test_async_resolve_legacy_fallback(self):
        """memory_sources未定義 → レガシーフォールバック"""
        config = CycleGenConfig()
        resolver = MemorySourceResolver()

        with patch.object(resolver, "_async_resolve_legacy") as mock_legacy:
            mock_legacy.return_value = [MemorySource(name="personal", is_local=True)]
            sources = await resolver.async_resolve(config)

        mock_legacy.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_resolve_legacy_sqlite(self):
        """レガシー旧方式 sqlite → ローカルソース"""
        config = CycleGenConfig()
        resolver = MemorySourceResolver()

        with patch.dict("os.environ", {}, clear=False):
            with patch("cyclegen.source.resolver.MemorySourceResolver._create_local_source") as mock_local:
                mock_local.return_value = MemorySource(name="personal", is_local=True)
                sources = await resolver._async_resolve_legacy(config)

        assert len(sources) == 1

    @pytest.mark.asyncio
    async def test_async_resolve_legacy_postgresql(self):
        """レガシー旧方式 postgresql → asyncpgソース"""
        config = CycleGenConfig()
        resolver = MemorySourceResolver()

        with patch.dict("os.environ", {"CYCLEGEN_PERSISTENCE": "postgresql", "CYCLEGEN_DATABASE_URL": "postgresql://test"}):
            with patch.object(resolver, "_async_create_postgresql_source") as mock_pg:
                mock_pg.return_value = MemorySource(name="personal", is_local=False)
                sources = await resolver._async_resolve_legacy(config)

        assert len(sources) == 1
        mock_pg.assert_called_once()


class TestMemorySource:
    """MemorySourceデータクラスのテスト"""

    def test_default_source_label(self):
        source = MemorySource(name="personal")
        assert source.source_label == "personal"

    def test_custom_source_label(self):
        source = MemorySource(name="org", source_label="organization")
        assert source.source_label == "organization"

    def test_is_local_default(self):
        source = MemorySource(name="test")
        assert source.is_local is False
