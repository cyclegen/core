"""SaaSリクエストスコープのContextVar定義

SaaSAuthMiddlewareがリクエストごとにowner_idとcurrent_userを設定し、
下流のPersistence/Guard層がContextVar経由で参照する。

CYCLE8.1: 初期実装
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyclegen.saas.models import SaaSUser

# リクエストスコープでowner_idを保持
current_owner_id: ContextVar[str] = ContextVar("current_owner_id")

# リクエストスコープでSaaSUserオブジェクトを保持（Quota/レート制限で使用）
current_user: ContextVar[SaaSUser] = ContextVar("current_user")


def is_saas_mode() -> bool:
    """現在のリクエストがSaaSモードかどうかを判定する。

    CYCLEGEN_MODE=saas の場合のみTrue。
    PaaSモードではguard（Quota/レート制限）は不要なのでFalseを返す。
    """
    import os
    return os.environ.get("CYCLEGEN_MODE") == "saas"
