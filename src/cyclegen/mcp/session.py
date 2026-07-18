"""mcp/session.py — MCPセッション単位のsession_id管理（CYCLE13.2 FR031 Phase 1）

検索（search）と利用（recall_used）イベントを紐付けるsession_idを提供する。
最初の利用時（通常は最初のmemory_search）に生成し、プロセス存続中は同一IDを
再利用する。session_idはevent_logのdetails（JSON）に格納されるため、
event_logテーブルのスキーマ変更は不要。

これにより「この検索で返されたN件のうち、何件が実際に使われたか」を
セッション単位で正確に計測でき、Memory Precisionをセッション別に算出できる。

制約: モジュールグローバルで保持するため、stdio transport（Personal層・
1プロセス1クライアント）を前提とする。remote transport（streamable-http）で
1プロセスが複数クライアントを捌く場合は session_id が共有される。FR031 Phase 1は
ローカル計測が対象のため許容する（将来Phaseでリクエストコンテキスト由来に変更検討）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

_current_session_id: str | None = None


def _generate() -> str:
    """`sess_{YYYYMMDD_HHMMSS}_{8桁hex}` 形式のsession_idを生成する。

    記憶ID（mem_{timestamp}_{hex}）と同系統の可読フォーマットに揃える。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = uuid.uuid4().hex[:8]
    return f"sess_{ts}_{rand}"


def get_session_id() -> str:
    """現在のsession_idを返す。未生成なら生成して保持する。"""
    global _current_session_id
    if _current_session_id is None:
        _current_session_id = _generate()
    return _current_session_id


def reset_session_id() -> None:
    """session_idをリセットする（テスト用・新セッション強制用）。"""
    global _current_session_id
    _current_session_id = None
