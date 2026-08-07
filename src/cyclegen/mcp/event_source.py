"""mcp/event_source.py — イベントの出所（source）の判定（CYCLE20.5 / FR062①-a）

CycleGenは「使われた履歴」そのものが品質指標である（dismiss率・boost率・捕捉率は
すべて event_log の集計）。そのため **機能を確かめる操作と、機能が測っている操作が、
同じ器を共有している**。CYCLE19.7では実発火の確認でdismissを5回呼んだだけで、
母艦の dismiss率が 0.00%🔴 → 1.56%🟡 と判定色まで動いた。

そこで dismiss / boost / archive のイベントに「誰が何のために呼んだか」を書く。

| source | 意味 |
|---|---|
| `explicit` | 利用者がその場で判断した（本来の利用） |
| `maintenance` | 掃除・一括操作（`cycle_complete` の提示から実行した等） |
| `verification` | 受入確認・テスト・デモ（開発者の操作） |

**掃除は利用ではない。** 空振り常連12件をまとめてdismissすると、
それだけで「利用者が活発にフィードバックしている」ように見える。

★ 既存イベント（source の無いもの）を `explicit` として扱ってはならない
（FR062 受入条件3）。推測で埋めると「記録がある」と誤認され、
本当に壊れたときに検知できなくなる（CYCLE19.2 A8で確立した規律）。
本モジュールは**新しく発行するイベントにしか関与しない**。
"""

from __future__ import annotations

import os

SOURCE_EXPLICIT = "explicit"
SOURCE_MAINTENANCE = "maintenance"
SOURCE_VERIFICATION = "verification"

VALID_SOURCES = (SOURCE_EXPLICIT, SOURCE_MAINTENANCE, SOURCE_VERIFICATION)

# プロセス全体を「検証中」と宣言するための環境変数。
# 受入スクリプトやデモのように、1回ずつ引数を渡せない場面のための経路。
# 例: CYCLEGEN_EVENT_SOURCE=verification uvx --from cyclegen cyclegen-mcp
ENV_VAR = "CYCLEGEN_EVENT_SOURCE"

# cycle_complete が「空振り常連」として提示した記憶ID（セッション内で保持）。
# ここに載っているIDへの dismiss / archive は、利用者がその場で思いついた判断ではなく
# **システムの提示に応じた掃除**なので maintenance として記録する（FR062 受入条件4）。
_suggested_ids: set[str] = set()


def note_suggested(memory_ids) -> None:
    """掃除の候補として提示した記憶IDを記録する（`cycle_complete` から呼ぶ）。"""
    _suggested_ids.update(memory_ids)


def is_suggested(memory_id: str) -> bool:
    """その記憶が掃除の候補として提示済みかを返す。"""
    return memory_id in _suggested_ids


def reset() -> None:
    """提示済みの記録を消す（テスト用・新セッション強制用）。"""
    _suggested_ids.clear()


def env_source() -> str | None:
    """環境変数によるプロセス全体の宣言を返す。未設定・不正値なら None。"""
    value = (os.environ.get(ENV_VAR) or "").strip().lower()
    return value if value in VALID_SOURCES else None


def resolve(memory_id: str, requested: str | None = None) -> tuple[str, str | None]:
    """イベントに書く source を決める。

    優先順位:
      1. 環境変数（プロセス全体の宣言）— 検証中のプロセスがやることは全部検証である
      2. 呼び出し側の明示指定（`source` 引数）
      3. `cycle_complete` が掃除の候補として提示済み → maintenance
      4. 既定 → explicit

    Returns:
        (source, warning)。warning は不正な値を渡されたときだけ文字列が入る
        （黙って捨てない。呼び出し側の取り違えに気づけるようにする）。
    """
    warning: str | None = None
    normalized = (requested or "").strip().lower() or None
    if normalized is not None and normalized not in VALID_SOURCES:
        warning = (
            f"⚠ 未知のsource '{requested}' は無視しました"
            f"（有効な値: {', '.join(VALID_SOURCES)}）"
        )
        normalized = None

    declared = env_source()
    if declared is not None:
        return declared, warning
    if normalized is not None:
        return normalized, warning
    if is_suggested(memory_id):
        return SOURCE_MAINTENANCE, warning
    return SOURCE_EXPLICIT, warning
