"""MCPツール層ガード関数

各MCPツールの先頭で呼び出し、Quota/レート制限をチェックする。
SaaSモード以外ではno-op（何もしない）。

CYCLE8.3: 初期実装
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyclegen.saas.context import is_saas_mode

if TYPE_CHECKING:
    from cyclegen.persistence.base import PersistenceAdapter


async def guard_store(persistence: PersistenceAdapter) -> None:
    """memory_store / memory_bulk_import 前に呼ぶガード。

    Quota + レート制限（memory_store）をチェック。
    """
    if not is_saas_mode():
        return

    from cyclegen.saas.context import current_user
    from cyclegen.saas.quota import check_quota
    from cyclegen.saas.rate_limit import check_rate_limit

    user = current_user.get()
    await check_quota(user, persistence)
    check_rate_limit(user.id, "memory_store")


async def guard_store_bulk(persistence: PersistenceAdapter, import_count: int) -> None:
    """memory_bulk_import 前に呼ぶガード。

    Quota（残枠チェック） + レート制限（memory_store）。
    """
    if not is_saas_mode():
        return

    from cyclegen.saas.context import current_user
    from cyclegen.saas.quota import check_quota_bulk
    from cyclegen.saas.rate_limit import check_rate_limit

    user = current_user.get()
    await check_quota_bulk(user, persistence, import_count)
    check_rate_limit(user.id, "memory_store")


async def guard_search() -> None:
    """memory_search 前に呼ぶガード。

    レート制限（memory_search）のみ。
    """
    if not is_saas_mode():
        return

    from cyclegen.saas.context import current_user
    from cyclegen.saas.rate_limit import check_rate_limit

    user = current_user.get()
    check_rate_limit(user.id, "memory_search")


async def guard_general() -> None:
    """その他のツール前に呼ぶガード。

    レート制限（全ツール合計）のみ。
    """
    if not is_saas_mode():
        return

    from cyclegen.saas.context import current_user
    from cyclegen.saas.rate_limit import check_rate_limit

    user = current_user.get()
    check_rate_limit(user.id, "general")
