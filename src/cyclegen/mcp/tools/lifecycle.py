"""mcp/tools/lifecycle.py — ライフサイクルツール（Core）

実装計画書§7.2: status / cycle_complete
CYCLE7.3.2: 選択的昇格（PromotionCriteria準拠。cycle_completeの候補提示に使用）
CYCLE7.7.3.1: async化

【Core/Enterprise境界】組織昇格の実行ツール（memory_promote / promotion_approve /
promotion_reject）はEnterprise専用（15.0論点A）のためCoreには同梱しない。
cycle_completeはOrg無効時に候補ゼロ・完了記録のみへグレースフル劣化する。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

import cyclegen.mcp.server as _server
from cyclegen.mcp.server import _async_get_system, mcp
from cyclegen.models import EventType, Memory, PromotionCriteria, PromotionReason

# mark_used自動推定用の正規表現
_MEMORY_ID_PATTERN = re.compile(r"mem_\d{8}_\d{6}_[0-9a-f]{8}")

# 昇格関連タグ
PROMOTED_TAG = "promoted:org"
PENDING_TAG = "promotion:pending"
REJECTION_TAG_PREFIX = "promotion:rejected:"

# 設定
REJECTION_COOLDOWN_DAYS = 30
MAX_CANDIDATES_DISPLAY = 5
CONTENT_PREVIEW_CHARS = 80


def _is_rejection_active(memory: Memory, now: Optional[datetime] = None) -> bool:
    """30日以内に却下された記憶は再サジェストしない。"""
    if now is None:
        now = datetime.now()
    for tag in memory.tags:
        if tag.startswith(REJECTION_TAG_PREFIX):
            try:
                rejected_date = datetime.strptime(tag[len(REJECTION_TAG_PREFIX):], "%Y-%m-%d")
                if (now - rejected_date) < timedelta(days=REJECTION_COOLDOWN_DAYS):
                    return True
            except ValueError:
                continue
    return False


def _select_promotable_memories(
    memories: list[Memory],
    criteria: PromotionCriteria,
    now: Optional[datetime] = None,
) -> list[Memory]:
    """昇格基準に基づいて昇格候補の記憶を選別する。

    - アーカイブ済みは除外
    - 既に昇格済み（promoted:orgタグ）は除外
    - 既にpending（promotion:pendingタグ）は除外
    - 却下クールダウン中は除外
    - PromotionCriteria.is_promotable()で判定
    """
    promotable = []
    for mem in memories:
        if mem.archived:
            continue
        if PROMOTED_TAG in mem.tags:
            continue
        if PENDING_TAG in mem.tags:
            continue
        if _is_rejection_active(mem, now):
            continue
        if criteria.is_promotable(mem):
            promotable.append(mem)
    return promotable


def _format_candidate(mem: Memory, reason: str) -> str:
    """候補1件のフォーマット。"""
    preview = mem.content[:CONTENT_PREVIEW_CHARS]
    if len(mem.content) > CONTENT_PREVIEW_CHARS:
        preview += "..."
    return (
        f"  [{mem.id}] L{mem.coordinates.layer}/P{mem.coordinates.priority:.2f}/C:{mem.coordinates.context}\n"
        f"    内容: {preview}\n"
        f"    理由: {reason}"
    )


@mcp.tool()
async def memory_status() -> str:
    """3次元記憶の状態を表示する（Personal + Org両方）。"""
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, _ = await _async_get_system()
    all_memories = await system.persistence.async_load_all(include_archived=True)
    active = [m for m in all_memories if not m.archived]
    archived = [m for m in all_memories if m.archived]
    pinned = [m for m in active if m.pinned]

    lines = [
        "=== CycleGen 3次元記憶ステータス ===",
        f"Personal Layer: {len(active)}件（ピン留め: {len(pinned)}, アーカイブ: {len(archived)}）",
    ]
    # Layer分布
    for layer in range(5, 0, -1):
        count = sum(1 for m in active if m.coordinates.layer == layer)
        lines.append(f"  L{layer}: {count}件")

    if _server._config and _server._config.org_server_enabled:
        from cyclegen.org.client import OrgClient

        org = OrgClient(_server._config)
        try:
            org_status = org.status()
            lines.append(
                f"Org Layer: {org_status.get('total_memories', '?')}件"
            )
        except Exception:
            lines.append("Org Layer: 接続不可")
    else:
        lines.append("Org Layer: 無効（org_server.enabled=false）")

    return "\n".join(lines)


@mcp.tool()
async def cycle_complete(summary: str, cycle_id: Optional[str] = None) -> str:
    """CYCLE完了を記録し、昇格基準を満たす記憶を候補として提示する（HITL）。

    ★CYCLEの作業が完了した時に呼ぶこと。

    昇格基準:
    - Priority >= 0.7 かつ Layer >= 3 かつ access_count >= 2
    - pinned=true → 無条件候補
    - import:* タグ → 候補にしない
    - 既に昇格済み / pending / 却下30日以内 → スキップ

    候補は promotion:pending タグが付与され、利用者の承認を待つ。
    承認: promotion_approve(memory_id)
    却下: promotion_reject(memory_id)

    Args:
        summary: CYCLE完了報告書の内容（Markdown全文）
        cycle_id: CYCLE番号（例: "CYCLE3.7"）。省略時は自動生成
    """
    from cyclegen.saas.guard import guard_general
    system, _, event_logger = await _async_get_system()
    await guard_general()

    # イベントログにCYCLE完了を記録（記憶の直接保存はしない — CYCLE12再定義）
    await event_logger.async_log(EventType.STORE, details={"cycle_id": cycle_id, "action": "cycle_complete"})

    lines = [
        f"CYCLE完了記録",
        f"  CYCLE: {cycle_id or '(自動)'}",
        "",
        "--- 記憶の保存について ---",
        "報告書の内容を意味単位に分割し、それぞれ memory_store で個別に保存してください。",
        "  - 各記憶に Layer と Context を付与（Priorityは0.3固定で自動設定）",
        "  - 1件の報告書を丸ごと1記憶にしない。設計判断・技術発見・試行錯誤など性質ごとに分ける",
        "  - 利用者の思考パターンの観察があれば L4-5 で保存",
    ]

    # mark_used自動推定（FR008）: summaryに含まれるmemory IDを検出
    # CYCLE13.3サブ計画 第1手: session_idを付与しsession別Precisionに寄与させる。
    # source="cycle_complete_auto"で明示mark_used（memory.py）と区別可能にする（将来の明示/推定別集計用）。
    from cyclegen.mcp.session import get_session_id
    session_id = get_session_id()
    mentioned_ids = sorted(set(_MEMORY_ID_PATTERN.findall(summary)))
    mark_used_count = 0
    for mid in mentioned_ids:
        mem = await system.persistence.async_load(mid)
        if mem is not None:
            await event_logger.async_log(
                EventType.RECALL_USED,
                mid,
                {"session_id": session_id, "source": "cycle_complete_auto"},
            )
            mark_used_count += 1
    lines.append(f"\nmark_used自動推定: {mark_used_count}件")

    # Org Layer への昇格候補提示（org_server.enabled=true 時のみ）
    if _server._config and _server._config.org_server_enabled:
        criteria = PromotionCriteria()
        all_personal = await system.persistence.async_load_all()

        # 既存pending候補を収集（昇格済みタグとの重複を補正）
        existing_pending = []
        for m in all_personal:
            if PENDING_TAG not in m.tags:
                continue
            if PROMOTED_TAG in m.tags:
                # promoted:org と promotion:pending が共存 → pending除去して補正
                corrected_tags = [t for t in m.tags if t != PENDING_TAG]
                await system.persistence.async_update(m.id, {"tags": corrected_tags})
                continue
            existing_pending.append(m)

        # 新規候補を選別
        new_candidates = _select_promotable_memories(all_personal, criteria)

        # 新規候補にpendingタグ付与
        for mem in new_candidates:
            new_tags = mem.tags + [PENDING_TAG]
            await system.persistence.async_update(mem.id, {"tags": new_tags})
            await event_logger.async_log(
                EventType.PROMOTION_SUGGESTED, mem.id, {"cycle_id": cycle_id}
            )

        # 統合表示（既存pending + 新規候補）
        all_candidates = existing_pending + new_candidates
        if not all_candidates:
            lines.append(f"\n昇格候補: 0件（基準を満たす記憶なし）")
        else:
            lines.append(f"\n--- 昇格候補 ---")
            lines.append(f"以下の記憶が組織への共有基準を満たしています。")
            lines.append(f"承認する場合は「昇格して」、却下する場合は「却下」と伝えてください。")
            lines.append("")

            displayed = all_candidates[:MAX_CANDIDATES_DISPLAY]
            for i, mem in enumerate(displayed, 1):
                # 理由文を生成
                if mem.pinned:
                    reason = "pinned（重要マーク）"
                else:
                    reason = f"P>={criteria.priority_min} & L>={criteria.layer_min} & 参照{mem.access_count}回"
                if mem in existing_pending:
                    reason += "（前回から継続）"
                lines.append(f"{i}. {_format_candidate(mem, reason)}")
                lines.append("")

            if len(all_candidates) > MAX_CANDIDATES_DISPLAY:
                lines.append(f"  ...他{len(all_candidates) - MAX_CANDIDATES_DISPLAY}件")

            lines.append(f"承認: promotion_approve(memory_id)")
            lines.append(f"却下: promotion_reject(memory_id)")
    else:
        lines.append(f"\nOrg Layer: 無効（org_server.enabled=false）")

    return "\n".join(lines)

