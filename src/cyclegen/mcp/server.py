"""mcp/server.py — FastMCPサーバー初期化

実装計画書§7.1: ローカルMCPサーバーのエントリポイント。
遅延初期化で MemorySystem3D + IntegratedSearchValve + EventLogger を構築する。

CYCLE7.7.2: Memory Source Resolver経由の初期化に変更（設計書v2 §1.3）。
CYCLE7.7.3.1: async初期化対応。_async_get_system()追加。
CYCLE8.4: SaaSモード統合（CYCLEGEN_MODE=saas時にミドルウェア+OwnerScopedPersistence）。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from typing import Union

    from cyclegen.core.memory_system import MemorySystem3D
    from cyclegen.models import CycleGenConfig
    from cyclegen.monitoring.event_log import AsyncEventLogger, EventLogger
    from cyclegen.search.valve import IntegratedSearchValve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,  # stdoutはJSON-RPC通信用
)

import os as _os

# リモートtransport時はDNS rebinding保護を無効化（ngrok等のトンネル対応）
_transport_env = _os.environ.get("CYCLEGEN_TRANSPORT", "stdio")
_is_remote = _transport_env in ("sse", "streamable-http") or "--transport" in sys.argv

mcp = FastMCP("cyclegen", host="0.0.0.0") if _is_remote else FastMCP("cyclegen")

# グローバル状態（遅延初期化）
_system: MemorySystem3D | None = None
_valve: IntegratedSearchValve | None = None
_event_logger: EventLogger | AsyncEventLogger | None = None
_config: CycleGenConfig | None = None


def _get_system() -> tuple[MemorySystem3D, IntegratedSearchValve, EventLogger]:
    """MemorySystem3D + Valve + EventLogger の遅延初期化。

    Memory Source Resolverで記憶ソースを解決し、
    IntegratedSearchValveをN-Sourceモードで構築する。
    memory_sourcesセクション未定義時は旧方式にフォールバック。
    """
    global _system, _valve, _event_logger, _config

    if _system is None:
        from cyclegen.config import load_config, load_contexts, resolve_home
        from cyclegen.core.classifier import AutoLayerClassifier
        from cyclegen.core.context import ContextSelector
        from cyclegen.core.layer import LayerHierarchy
        from cyclegen.core.memory_system import MemorySystem3D
        from cyclegen.core.priority import PriorityManager
        from cyclegen.monitoring.event_log import EventLogger
        from cyclegen.search.cognitive_load import CognitiveLoadManager
        from cyclegen.search.engine import SearchEngine
        from cyclegen.search.valve import IntegratedSearchValve
        from cyclegen.source.resolver import MemorySourceResolver

        _config = load_config()
        resolver = MemorySourceResolver()
        sources = resolver.resolve(_config)

        # primary source = 最初のソース（store/update/delete用）
        primary = sources[0]

        if primary.backend is not None:
            persistence = primary.backend
        else:
            # cloudのみ構成（通常はない）— フォールバック
            from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
            home = resolve_home(_config)
            persistence = MdWithSQLitePersistence(home)
            persistence.sync_from_md()

        # Context読み込み
        if primary.is_local:
            contexts = load_contexts(_config)
        else:
            from cyclegen.config import DEFAULT_CONTEXTS
            from cyclegen.models import ContextDefinition
            contexts = {
                name: ContextDefinition(**definition)
                for name, definition in DEFAULT_CONTEXTS.items()
            }

        # CYCLE12.7.6: EmbeddingManager + ContextAffinityResolver接続
        from cyclegen.search.context_affinity import ContextAffinityResolver
        from cyclegen.search.context_detector import ContextAutoDetector
        from cyclegen.search.embedding import EmbeddingManager

        home = resolve_home(_config)
        contexts_yaml = home / _config.contexts_file
        embedding_manager = EmbeddingManager.create()
        affinity_resolver = ContextAffinityResolver.from_yaml(contexts_yaml)

        # CYCLE12.7.8: Context自動判定（embedding類似度ベース）
        context_detector = None
        if embedding_manager is not None:
            context_detector = ContextAutoDetector.from_yaml(
                contexts_yaml, embedding_manager
            )

        search_engine = SearchEngine(
            embedding_manager=embedding_manager,
            affinity_resolver=affinity_resolver,
        )

        # 各SourceのSearchEngineにも新モード（semantic+affinity）を注入
        for source in sources:
            if source.search_engine is not None:
                source.search_engine = search_engine

        _system = MemorySystem3D(
            persistence=persistence,
            layer_hierarchy=LayerHierarchy(),
            priority_manager=PriorityManager(),
            context_selector=ContextSelector(contexts),
            classifier=AutoLayerClassifier(),
            search_engine=search_engine,
            embedding_manager=embedding_manager,
            context_detector=context_detector,
        )

        cognitive_load = CognitiveLoadManager(_config.default_max_items)

        # N-Sourceモードで構築
        _valve = IntegratedSearchValve.from_sources(
            sources=sources,
            cognitive_load=cognitive_load,
            valve_config=_config.valve,
        )

        _event_logger = EventLogger(persistence.conn)

    assert _system is not None
    assert _valve is not None
    assert _event_logger is not None
    return _system, _valve, _event_logger


async def _async_get_system() -> tuple["MemorySystem3D", "IntegratedSearchValve", "EventLogger | AsyncEventLogger"]:
    """MemorySystem3D + Valve + EventLogger の非同期遅延初期化。

    PostgreSQLバックエンド使用時はasync_resolve()でasyncpg初期化する。
    ローカル（SQLite）のみの場合は同期版と同じ結果になる。
    CYCLE8.4: SaaSモード時にOwnerScopedPersistenceでラップ。
    """
    global _system, _valve, _event_logger, _config

    if _system is None:
        import os

        from cyclegen.config import load_config, load_contexts, resolve_home
        from cyclegen.core.classifier import AutoLayerClassifier
        from cyclegen.core.context import ContextSelector
        from cyclegen.core.layer import LayerHierarchy
        from cyclegen.core.memory_system import MemorySystem3D
        from cyclegen.core.priority import PriorityManager
        from cyclegen.monitoring.event_log import AsyncEventLogger, EventLogger
        from cyclegen.search.cognitive_load import CognitiveLoadManager
        from cyclegen.search.engine import SearchEngine
        from cyclegen.search.valve import IntegratedSearchValve
        from cyclegen.source.resolver import MemorySourceResolver

        _config = load_config()
        resolver = MemorySourceResolver()
        sources = await resolver.async_resolve(_config)

        primary = sources[0]

        if primary.backend is not None:
            persistence = primary.backend
        else:
            from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
            home = resolve_home(_config)
            persistence = MdWithSQLitePersistence(home)
            persistence.sync_from_md()

        if primary.is_local:
            contexts = load_contexts(_config)
        else:
            from cyclegen.config import DEFAULT_CONTEXTS
            from cyclegen.models import ContextDefinition
            contexts = {
                name: ContextDefinition(**definition)
                for name, definition in DEFAULT_CONTEXTS.items()
            }

        # SaaS/PaaSモード: OwnerScopedPersistenceでラップ
        _mode = os.environ.get("CYCLEGEN_MODE", "")
        if _mode in ("saas", "paas"):
            from cyclegen.saas.persistence_wrapper import OwnerScopedPersistence
            persistence = OwnerScopedPersistence(persistence)

        # CYCLE12.7.6: EmbeddingManager + ContextAffinityResolver接続
        from cyclegen.search.context_affinity import ContextAffinityResolver
        from cyclegen.search.context_detector import ContextAutoDetector
        from cyclegen.search.embedding import EmbeddingManager

        home = resolve_home(_config)
        contexts_yaml = home / _config.contexts_file
        embedding_manager = EmbeddingManager.create()
        affinity_resolver = ContextAffinityResolver.from_yaml(contexts_yaml)

        # CYCLE12.7.8: Context自動判定（embedding類似度ベース）
        context_detector = None
        if embedding_manager is not None:
            context_detector = ContextAutoDetector.from_yaml(
                contexts_yaml, embedding_manager
            )

        search_engine = SearchEngine(
            embedding_manager=embedding_manager,
            affinity_resolver=affinity_resolver,
        )

        # 各SourceのSearchEngineにも新モード（semantic+affinity）を注入
        for source in sources:
            if source.search_engine is not None:
                source.search_engine = search_engine

        _system = MemorySystem3D(
            persistence=persistence,
            layer_hierarchy=LayerHierarchy(),
            priority_manager=PriorityManager(),
            context_selector=ContextSelector(contexts),
            classifier=AutoLayerClassifier(),
            search_engine=search_engine,
            embedding_manager=embedding_manager,
            context_detector=context_detector,
        )

        cognitive_load = CognitiveLoadManager(_config.default_max_items)

        _valve = IntegratedSearchValve.from_sources(
            sources=sources,
            cognitive_load=cognitive_load,
            valve_config=_config.valve,
        )

        # EventLogger: asyncpgバックエンドの場合はAsyncEventLogger
        # SaaSモード時: OwnerScopedPersistenceの内部persistenceからプールを取得
        _inner_persistence = persistence._inner if hasattr(persistence, '_inner') else persistence
        if hasattr(_inner_persistence, '_pool') and _inner_persistence._pool is not None:
            _event_logger = AsyncEventLogger(pool=_inner_persistence._pool)
            await _event_logger.init_table()
        else:
            _event_logger = EventLogger(_inner_persistence.conn)

    assert _system is not None
    assert _valve is not None
    assert _event_logger is not None
    return _system, _valve, _event_logger


def _get_config() -> "CycleGenConfig":
    """設定オブジェクトを返す（_get_systemの副作用で初期化済み）。"""
    global _config
    if _config is None:
        _get_system()  # 初期化を発火
    assert _config is not None
    return _config


def register_tools() -> None:
    """MCPツールを登録する（インポート副作用で@mcp.toolが登録される）。"""
    from cyclegen.mcp.tools import bulk_import, diagnostics, lifecycle, memory  # noqa: F401

    # optional extras: docx（python-docx 未インストール時はスキップ）
    from cyclegen.mcp.tools.finish import register_finish_tools

    register_finish_tools(mcp)


def _preimport_embedding() -> None:
    """イベントループに入る前に、単一スレッドで fastembed を import しておく（CYCLE17.6.4.1 / F-22）。

    ★なぜ要るか:
      遅延importのままだと、MCPのワーカースレッドと主スレッドが**同時に**
      `import fastembed` → `numpy` の C拡張(.pyd/.so) をロードしにいく。
      Pythonのimportロックとネイティブ拡張の初期化が噛み合ってデッドロックし、
      最初のツール呼び出しが**永久に返らない**（WIN-01で実測：150秒タイムアウト → 0.05秒）。

    ★なぜ Windows で出るか:
      importそのものが極端に遅く（母艦 0.27s に対し WIN-01 は 10.30s）、
      **競合の窓が38倍**になる。競合なので**間欠的**で、運が良ければ通ってしまう。

    ★範囲を意図的に狭くしている:
      `EmbeddingManager.create()` がするのは `import fastembed` **だけ**で、
      モデルのロード・ダウンロードはしない（`_ensure_model()` は遅延のまま）。
      「起動時にぜんぶ温める」にすると初回モデルDL（約120秒）まで起動時に来て、
      今度はホスト側の起動タイムアウトに掛かる。**importの競合だけを潰す。**

    ★失敗しても起動は止めない:
      fastembed 未導入（`semantic` extra なし）でも `create()` は None を返す設計だが、
      それ以外の想定外（壊れたwheel等）でもサーバ起動そのものは続けるべきなので握る。
      デッドロックの回避は最適化であって、起動の前提条件ではない。

    ★F-17（初回導入でMCP起動失敗）も同じ競合で説明がつく（CYCLE17.6.4.1）。
    """
    try:
        from cyclegen.search.embedding import EmbeddingManager

        EmbeddingManager.create()
    except Exception:  # noqa: BLE001 — 起動そのものは止めない
        logging.debug("embedding の preimport をとばしました", exc_info=True)


def main() -> None:
    """MCPサーバーを起動する。

    CLI引数または環境変数でtransportを切替:
      cyclegen-mcp                    → stdio（デフォルト、Claude Code用）
      cyclegen-mcp --transport sse    → SSE（Web AIクライアント用）
      cyclegen-mcp --transport streamable-http → Streamable HTTP
      CYCLEGEN_TRANSPORT=sse cyclegen-mcp      → 環境変数でも指定可
    """
    import os

    register_tools()

    # CLI引数 > 環境変数 > デフォルト(stdio)
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]
    else:
        transport = os.environ.get("CYCLEGEN_TRANSPORT", "stdio")

    valid_transports = ("stdio", "sse", "streamable-http")
    if transport not in valid_transports:
        logging.error(
            "不明なtransport: '%s'（有効値: %s）", transport, ", ".join(valid_transports)
        )
        sys.exit(1)

    # リモートtransport時はホストを0.0.0.0にバインド（ngrok等のトンネル対応）
    if transport in ("sse", "streamable-http"):
        mcp.settings.host = os.environ.get("CYCLEGEN_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("CYCLEGEN_PORT", "8000"))

    # ★F-22: イベントループに入る前に、単一スレッドで fastembed を import しておく。
    #   transportの検証が終わってから呼ぶ（不正なtransportで終了するときに10秒待たせない）。
    _preimport_embedding()

    logging.info("CycleGen MCPサーバー起動 (transport=%s)", transport)

    # Streamable HTTP時はCORS設定を追加してから起動（claude.ai等からのブラウザ接続対応）
    if transport == "streamable-http":
        cors_origins = os.environ.get("CYCLEGEN_CORS_ORIGINS", "*")

        async def _run_with_cors() -> None:
            import uvicorn
            from starlette.middleware.cors import CORSMiddleware

            app = mcp.streamable_http_app()
            origins = [o.strip() for o in cors_origins.split(",")]
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"],
            )
            logging.info("CORS設定: origins=%s", origins)

            # PaaSモード: 軽量owner_id注入ミドルウェア
            mode = os.environ.get("CYCLEGEN_MODE", "")
            if mode == "paas":
                from cyclegen.paas.middleware import PaaSOwnerMiddleware
                app.add_middleware(PaaSOwnerMiddleware)
                logging.info("PaaSモード有効: PaaSOwnerMiddleware追加")

            # SaaSモード: 認証ミドルウェア + REST API + SaaSDB初期化
            if mode == "saas":
                import asyncpg
                from cyclegen.saas.db import SaaSDB
                from cyclegen.saas.key_manager import KeyManager
                from cyclegen.saas.middleware import SaaSAuthMiddleware
                from cyclegen.saas.web_api import create_saas_api

                saas_dsn = os.environ.get(
                    "CYCLEGEN_SAAS_DB_URL",
                    os.environ.get("CYCLEGEN_DATABASE_URL", ""),
                )
                pool = await asyncpg.create_pool(saas_dsn)
                saas_db = SaaSDB(pool)
                await saas_db.init_tables()

                # SaaS管理用REST API（/saas/api/v1/*）
                key_mgr = KeyManager(saas_db)
                saas_api = create_saas_api(saas_db, key_mgr)
                app.mount("/saas/api/v1", saas_api)
                logging.info("SaaS REST API マウント: /saas/api/v1")

                # 認証ミドルウェア（/saas/* はスキップ）
                app.add_middleware(SaaSAuthMiddleware, db=saas_db)
                logging.info("SaaSモード有効: 認証ミドルウェア追加")

            config = uvicorn.Config(
                app,
                host=mcp.settings.host,
                port=mcp.settings.port,
                log_level=mcp.settings.log_level.lower(),
            )
            server = uvicorn.Server(config)
            await server.serve()

        asyncio.run(_run_with_cors())
    else:
        mcp.run(transport=transport)


def main_sse() -> None:
    """SSEモードでMCPサーバーを起動する（cyclegen-mcp-sse コマンド用）。

    ★入口は2つある（CYCLE20.7）: `cyclegen-mcp`（main）と `cyclegen-mcp-sse`（ここ）。
      同じ `mcp.run()` を通るので、F-22 の preimport も両方に要る。
    """
    register_tools()
    mcp.settings.host = "0.0.0.0"
    _preimport_embedding()
    logging.info("CycleGen MCPサーバー起動 (transport=sse)")
    mcp.run(transport="sse")
