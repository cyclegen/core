"""mcp/tools/diagnostics.py — モニタリング・メンテナンスツール

実装計画書§7.2: memory_diagnostics
CYCLE7.7.3.1: async化
CYCLE8.4: SaaS guard追加
CYCLE12.6: memory_recalculate追加（Ankiモデル + イベントソーシング）
CYCLE12.7.5: memory_reembed追加（既存記憶の一括embedding生成）
CYCLE12.7.8: memory_reclassify追加（既存記憶のContext再分類）
"""

from __future__ import annotations

import json
from collections import defaultdict

from cyclegen.core.priority import CURRENT_SCORE_VERSION, EventCounts, PriorityManager
from cyclegen.mcp.server import _async_get_system, mcp
from cyclegen.models import EventType
from cyclegen.monitoring.collector import DiagnosticsCollector


@mcp.tool()
async def memory_diagnostics(period_days: int = 30) -> str:
    """3次元記憶システムの診断レポートを表示する。

    座標分布・検索品質・昇格統計・Priority動的更新の実態を集計する。
    パラメータ（減衰率・ボーナス値・Miller's上限等）が適切かの検証に使用。

    Args:
        period_days: 集計対象期間（日数、デフォルト30日）
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    collector = DiagnosticsCollector(event_logger, system.persistence)
    report = collector.collect(period_days=period_days)

    lines = [
        "=== 3次元記憶 診断レポート ===",
        f"期間: 直近{period_days}日",
        f"総記憶数: {report.total_memories}",
        "",
        "--- Layer分布 ---",
    ]
    for layer in range(5, 0, -1):
        count = report.layer_distribution.get(layer, 0)
        lines.append(f"  L{layer}: {count}件")

    lines.append("\n--- Priority分布（区分） ---")
    for tier in ["high", "medium", "low", "archive"]:
        count = report.priority_distribution.get(tier, 0)
        lines.append(f"  {tier}: {count}件")

    if report.priority_detail:
        lines.append("\n--- Priority分布（詳細） ---")
        for p_val in sorted(report.priority_detail.keys(), key=float, reverse=True):
            count = report.priority_detail[p_val]
            lines.append(f"  P{p_val}: {count}件")

    lines.append(f"\nピン留め: {report.pinned_count}件")
    lines.append(f"アーカイブ: {report.archived_count}件")
    lines.append(f"未利用（access_count=0）: {report.access_count_zero}件")

    lines.append("\n--- 検索品質 ---")
    s = report.search_stats
    lines.append(f"  検索回数: {s.total_searches}")
    lines.append(f"  平均スコア: {s.avg_score:.1f}")
    lines.append(f"  boost: {s.boost_count}回 / dismiss: {s.dismiss_count}回")
    lines.append(f"  boost率: {s.boost_rate:.0%}")

    lines.append("\n--- 昇格統計 ---")
    p = report.promotion_stats
    lines.append(f"  昇格回数: {p.total_promotions}")
    lines.append(f"  昇格後の平均参照回数: {p.avg_post_promotion_access:.1f}")

    lines.append("\n--- 3指標（証明装置） ---")
    pr = report.precision_stats
    lines.append(f"  Memory Precision: {pr.precision_rate:.0%}（{pr.used_count}/{pr.total_recalled}）")
    lines.append(f"  boost率（検索品質）: {s.boost_rate:.0%}")
    lines.append(f"  平均検索スコア: {s.avg_score:.1f}")

    # セッション別Precision（CYCLE13.2 FR031 P1）
    if pr.session_count > 0:
        lines.append(
            f"  セッション別Precision: {pr.avg_session_precision:.0%}"
            f"（{pr.session_count}セッション平均）"
        )

    if report.warnings:
        lines.append("\n--- 警告 ---")
        for w in report.warnings:
            lines.append(f"  ⚠ {w}")

    return "\n".join(lines)


@mcp.tool()
async def memory_recalculate(dry_run: bool = True) -> str:
    """Priorityをイベント履歴から再計算する（CYCLE12.6: Ankiモデル）。

    score_versionが古い記憶のPriorityを、event_logのイベント履歴から再導出する。
    将来のロジック変更時にも再実行すれば整合性が回復する。

    Args:
        dry_run: True=before/after比較レポートのみ表示（デフォルト）、False=実際に適用
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    pm = PriorityManager()

    # 全記憶を取得（アーカイブ含む）
    all_memories = await system.persistence.async_load_all(include_archived=True)

    # event_logから全期間のイベントを集計（memory_id別）
    event_counts_map: dict[str, EventCounts] = defaultdict(EventCounts)
    for event_type in [EventType.BOOST, EventType.DISMISS, EventType.RECALL_USED]:
        events = event_logger.get_events(event_type=event_type, since_days=36500)
        for e in events:
            if e.memory_id is None:
                continue
            ec = event_counts_map[e.memory_id]
            if event_type == EventType.BOOST:
                ec.boost += 1
            elif event_type == EventType.DISMISS:
                ec.dismiss += 1
            elif event_type == EventType.RECALL_USED:
                ec.mark_used += 1

    # 再計算対象の特定と計算
    targets = []
    for mem in all_memories:
        ec = event_counts_map.get(mem.id, EventCounts())
        # access_countはMemoryモデルに保持されている
        ec.access = mem.access_count
        new_priority = pm.recalculate(ec)
        if mem.coordinates.priority != new_priority or mem.score_version != CURRENT_SCORE_VERSION:
            targets.append({
                "id": mem.id,
                "old_priority": mem.coordinates.priority,
                "new_priority": new_priority,
                "old_sv": mem.score_version,
                "events": ec,
                "content_preview": mem.content[:50],
            })

    # before/afterレポート生成
    lines = [
        f"=== Priority再計算レポート (score_version → v{CURRENT_SCORE_VERSION}) ===",
        f"総記憶数: {len(all_memories)}",
        f"対象（変更あり）: {len(targets)}件",
        f"モード: {'dry-run（適用しない）' if dry_run else '適用'}",
        "",
    ]

    if targets:
        # Priority分布のbefore/after
        before_dist: dict[str, int] = defaultdict(int)
        after_dist: dict[str, int] = defaultdict(int)
        for t in targets:
            before_dist[f"{t['old_priority']:.1f}"] += 1
            after_dist[f"{t['new_priority']:.2f}"] += 1

        lines.append("--- 対象記憶のPriority分布変化 ---")
        lines.append("Before:")
        for p in sorted(before_dist.keys(), key=float, reverse=True):
            lines.append(f"  P{p}: {before_dist[p]}件")
        lines.append("After:")
        for p in sorted(after_dist.keys(), key=float, reverse=True):
            lines.append(f"  P{p}: {after_dist[p]}件")

        # 変動が大きい記憶トップ10
        targets_sorted = sorted(targets, key=lambda t: abs(t["new_priority"] - t["old_priority"]), reverse=True)
        lines.append(f"\n--- 変動幅トップ10 ---")
        for t in targets_sorted[:10]:
            delta = t["new_priority"] - t["old_priority"]
            ec = t["events"]
            lines.append(
                f"  {t['id']}: P{t['old_priority']:.2f} → P{t['new_priority']:.2f} "
                f"(delta={delta:+.2f}, access={ec.access}, used={ec.mark_used}, "
                f"boost={ec.boost}, dismiss={ec.dismiss})"
            )
            lines.append(f"    {t['content_preview']}...")

    if not dry_run and targets:
        # 実適用
        applied = 0
        for t in targets:
            success = await system.persistence.async_update(t["id"], {
                "coordinates.priority": t["new_priority"],
                "score_version": CURRENT_SCORE_VERSION,
            })
            if success:
                applied += 1
        lines.append(f"\n--- 適用結果 ---")
        lines.append(f"  適用成功: {applied}/{len(targets)}件")
        lines.append(f"  score_version: v{CURRENT_SCORE_VERSION}")
    elif not targets:
        lines.append("全記憶が最新のscore_versionで、Priorityの変更もありません。")

    return "\n".join(lines)


@mcp.tool()
async def memory_reembed(dry_run: bool = True) -> str:
    """既存記憶にembeddingを一括生成する（CYCLE12.7.5）。

    embeddingが未設定（None）の記憶に対してFastEmbedでembeddingを生成・保存する。
    fastembed未インストール時はエラーメッセージを返す。

    Args:
        dry_run: True=対象件数のみ表示（デフォルト）、False=実際に生成・保存
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    from cyclegen.search.embedding import EmbeddingManager

    emb_mgr = EmbeddingManager.create()
    if emb_mgr is None:
        return (
            "エラー: fastembed未インストール。\n"
            "`pip install cyclegen[semantic]` でインストールしてください。"
        )

    system, _, _ = await _async_get_system()

    # 全記憶を取得（アーカイブ含む）
    all_memories = await system.persistence.async_load_all(include_archived=True)
    targets = [m for m in all_memories if m.embedding is None]

    lines = [
        "=== embedding一括生成レポート ===",
        f"総記憶数: {len(all_memories)}",
        f"embedding未設定: {len(targets)}件",
        f"embedding設定済み: {len(all_memories) - len(targets)}件",
        f"モード: {'dry-run（生成しない）' if dry_run else '生成・保存'}",
    ]

    if not targets:
        lines.append("\n全記憶がembedding設定済みです。")
        return "\n".join(lines)

    if dry_run:
        lines.append(f"\n対象{len(targets)}件のembeddingを生成するには dry_run=false で実行してください。")
        return "\n".join(lines)

    # 一括embedding生成
    contents = [m.content for m in targets]
    embeddings = emb_mgr.embed_batch(contents)

    # 保存
    saved = 0
    for memory, embedding in zip(targets, embeddings):
        success = await system.persistence.async_update(memory.id, {
            "embedding": embedding,
        })
        if success:
            saved += 1

    lines.append(f"\n--- 生成結果 ---")
    lines.append(f"  生成・保存成功: {saved}/{len(targets)}件")

    return "\n".join(lines)


@mcp.tool()
async def memory_reclassify(dry_run: bool = True, threshold: float = 0.0) -> str:
    """既存記憶のContextをembedding類似度で再分類する（CYCLE12.7.8）。

    各記憶の内容と全Context説明文のembedding類似度を比較し、
    現在のContextと推奨Contextが異なる記憶を特定する。
    fastembed未インストール時はエラーメッセージを返す。

    Args:
        dry_run: True=変更候補のみ表示（デフォルト）、False=実際にContext更新
        threshold: 類似度差分の閾値。推奨Contextとの類似度が現在Contextとの類似度を
                   この値以上上回る場合のみ変更候補とする（デフォルト0.0=すべて表示）
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    from cyclegen.search.context_detector import ContextAutoDetector
    from cyclegen.search.embedding import EmbeddingManager

    emb_mgr = EmbeddingManager.create()
    if emb_mgr is None:
        return (
            "エラー: fastembed未インストール。\n"
            "`pip install cyclegen[semantic]` でインストールしてください。"
        )

    system, _, event_logger = await _async_get_system()

    # ContextAutoDetector構築
    from cyclegen.config import resolve_home
    from cyclegen.mcp.server import _get_config

    srv_config = _get_config()
    home = resolve_home(srv_config)
    contexts_yaml = home / srv_config.contexts_file

    detector = ContextAutoDetector.from_yaml(contexts_yaml, emb_mgr)
    if detector is None:
        return "エラー: enterprise_contexts.yamlにdescriptionフィールドがありません。"

    # 全記憶を取得（アーカイブ除外）
    all_memories = await system.persistence.async_load_all()

    candidates: list[tuple[str, str, str, float, float]] = []  # id, old, new, old_sim, new_sim

    for memory in all_memories:
        scores = detector.detect_with_scores(memory.content)
        if not scores:
            continue

        best_ctx, best_sim = scores[0]
        current_ctx = memory.coordinates.context

        # 現在のContextの類似度を取得
        current_sim = 0.0
        for ctx_name, sim in scores:
            if ctx_name == current_ctx:
                current_sim = sim
                break

        # 推奨が現在と異なり、閾値を超える場合のみ候補
        if best_ctx != current_ctx and (best_sim - current_sim) > threshold:
            candidates.append((memory.id, current_ctx, best_ctx, current_sim, best_sim))

    lines = [
        "=== Context再分類レポート ===",
        f"総記憶数: {len(all_memories)}",
        f"変更候補: {len(candidates)}件",
        f"閾値: {threshold:.2f}",
        f"モード: {'dry-run（変更しない）' if dry_run else '変更適用'}",
    ]

    if not candidates:
        lines.append("\n全記憶のContextが最適です。変更候補はありません。")
        return "\n".join(lines)

    lines.append("\n--- 変更候補 ---")
    for mem_id, old_ctx, new_ctx, old_sim, new_sim in candidates:
        lines.append(
            f"  {mem_id}: {old_ctx}({old_sim:.3f}) → {new_ctx}({new_sim:.3f})"
            f"  差分: +{new_sim - old_sim:.3f}"
        )

    if dry_run:
        lines.append(
            f"\n{len(candidates)}件を適用するには dry_run=false で実行してください。"
        )
        return "\n".join(lines)

    # 実際にContext更新
    updated = 0
    for mem_id, old_ctx, new_ctx, _, _ in candidates:
        success = await system.persistence.async_update(mem_id, {
            "coordinates.context": new_ctx,
        })
        if success:
            await event_logger.async_log(
                EventType.UPDATE,
                mem_id,
                {"action": "reclassify", "old_context": old_ctx, "new_context": new_ctx},
            )
            updated += 1

    lines.append(f"\n--- 適用結果 ---")
    lines.append(f"  更新成功: {updated}/{len(candidates)}件")

    return "\n".join(lines)
