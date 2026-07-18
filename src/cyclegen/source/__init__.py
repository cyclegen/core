"""source — Memory Source Resolver（設計書v2 §1.3）

宣言的設定から記憶ソース（Personal/Org/Team等）を解決し、
MCPサーバーが裏側の技術差異（SQLite/PostgreSQL/REST API）を吸収する。
"""

from cyclegen.source.memory_source import MemorySource
from cyclegen.source.resolver import MemorySourceResolver

__all__ = ["MemorySource", "MemorySourceResolver"]
