"""source/memory_source.py — 記憶ソースの抽象表現

設計書v2 §1.3: 1つの記憶ソース（personal/org/team等）を表現する。
バックエンドの技術差異（SQLite/PostgreSQL/REST API）を吸収する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyclegen.org.client import OrgClient
    from cyclegen.persistence.base import PersistenceAdapter
    from cyclegen.search.engine import SearchEngine


@dataclass
class MemorySource:
    """1つの記憶ソース。

    Attributes:
        name: ソース識別名（"personal", "org", "team" 等）
        backend: PersistenceAdapter準拠のバックエンド（cloudの場合はNone）
        search_engine: ソースごとのスコアリングエンジン（cloudの場合はNone）
        client: cloudバックエンド用のOrgClient（local/postgresqlの場合はNone）
        owner_id: テナント分離用（Noneまたは空文字なら共有）
        source_label: 検索結果の[personal]/[org]表示用ラベル
        is_local: ローカルソースか（local_bonus加算判定用）
    """

    name: str
    backend: PersistenceAdapter | None = None
    search_engine: SearchEngine | None = None
    client: OrgClient | None = None
    owner_id: str | None = None
    source_label: str = ""
    is_local: bool = False

    def __post_init__(self) -> None:
        if not self.source_label:
            self.source_label = self.name
