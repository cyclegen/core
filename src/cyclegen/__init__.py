"""CycleGen Enterprise — 3次元記憶システム"""

# 版数はインストール済みパッケージのメタデータ（＝pyproject の version）から引く。
# CYCLE17.3 まではここに文字列を直書きしており、15.12 で pyproject を 0.1.1.dev0 へ
# 上げた際に取り残されて "0.1.0" のままだった。同じ事実を2箇所で手管理すると
# 更新されない側が必ず出るので、単一の出所から導出する。
# 未インストール（リポジトリ直実行）では取得できないため既定値へ落とす。
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("cyclegen")
except PackageNotFoundError:  # pragma: no cover - インストールされていない実行経路
    __version__ = "0.0.0+unknown"

del _pkg_version, PackageNotFoundError


def create_memory_system(config_path=None, home=None):
    """MemorySystem3D を構築するファクトリ関数。

    Args:
        config_path: cyclegen_config.yaml のパス（省略時はデフォルト探索）
        home: ホームディレクトリ（省略時はconfig.homeから決定）

    Returns:
        MemorySystem3D インスタンス
    """
    from pathlib import Path

    from cyclegen.config import load_config, load_contexts, resolve_home
    from cyclegen.core.classifier import AutoLayerClassifier
    from cyclegen.core.context import ContextSelector
    from cyclegen.core.layer import LayerHierarchy
    from cyclegen.core.memory_system import MemorySystem3D
    from cyclegen.core.priority import PriorityManager
    from cyclegen.persistence.md_sqlite import MdWithSQLitePersistence
    from cyclegen.search.engine import SearchEngine

    config = load_config(Path(config_path) if config_path else None)
    if home:
        config.home = str(home)
    resolved_home = resolve_home(config)
    contexts = load_contexts(config)

    persistence = MdWithSQLitePersistence(resolved_home)
    persistence.sync_from_md()

    return MemorySystem3D(
        persistence=persistence,
        layer_hierarchy=LayerHierarchy(),
        priority_manager=PriorityManager(),
        context_selector=ContextSelector(contexts),
        classifier=AutoLayerClassifier(),
        search_engine=SearchEngine(weights=config.scoring_weights),
    )
