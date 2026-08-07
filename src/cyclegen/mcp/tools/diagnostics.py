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

import cyclegen.mcp.server as _server
from cyclegen.core.priority import (
    ARCHIVE_CANDIDATE_THRESHOLD,
    CURRENT_SCORE_VERSION,
    EventCounts,
    PriorityManager,
)
from cyclegen.mcp.server import _async_get_system, mcp
from cyclegen.models import EventType
from cyclegen.monitoring.collector import DiagnosticsCollector
from cyclegen.monitoring.idle_recall import MIN_SLOTS_FOR_ESTIMATE, IdleRecallAnalyzer

# 健康状態の判定閾値（CYCLE19.6 / A4）
# 出典: CYCLE19 健全性調査レポート §10-1 の C3・C4・C6。
# **母艦1台の実測から置いた暫定値**であり、2〜3回まわしてから調整する。
_UNRETURNED = (0.30, 0.50)  # 未返却率: これ未満なら🟢 / 🟡 / 超えたら🔴
_CONCENTRATION = (0.20, 0.35)  # 上位1%の返却スロット占有率
_DISMISS_RATE = (0.02, 0.005)  # dismiss率: 以上なら🟢 / 🟡 / 未満なら🔴（向きが逆）


def _judge_low_is_good(value: float, thresholds: tuple[float, float]) -> str:
    good, warn = thresholds
    if value < good:
        return "🟢"
    if value <= warn:
        return "🟡"
    return "🔴"


def _judge_high_is_good(value: float, thresholds: tuple[float, float]) -> str:
    good, warn = thresholds
    if value >= good:
        return "🟢"
    if value >= warn:
        return "🟡"
    return "🔴"


def _health_lines(report, idle, archive_candidates, active_ids: set) -> list[str]:
    """「記憶ストアの調子は？」に答える section（CYCLE19.6 / A4）。

    ★判定（🟢🟡🔴）は、判定できるだけのデータがあるときだけ出す。
    使い始めの利用者に「未返却率100% 🔴」と言うのは、
    調子が悪いのではなく**まだ測れていない**だけである（CYCLE19.5 知見1と同じ筋）。
    """
    lines = ["\n--- 記憶の健康状態（全期間） ---"]

    judgeable = idle.total_slots >= MIN_SLOTS_FOR_ESTIMATE
    if not judgeable:
        lines.append(
            f"  ※ 返却スロットが{idle.total_slots}件（{MIN_SLOTS_FOR_ESTIMATE}件未満）＝"
            "まだ判定できません。数値だけ出します"
        )

    def mark(symbol: str) -> str:
        return f"  {symbol}" if judgeable else ""

    # C3: 一度も検索で返っていない記憶（「未利用」とは別物）
    unreturned = idle.unreturned_ratio(active_ids)
    lines.append(
        f"  未返却率: {unreturned:.1%}"
        f"（{idle.returned_count(active_ids)}/{len(active_ids)}件は返却経験あり）"
        + mark(_judge_low_is_good(unreturned, _UNRETURNED))
    )

    # C4: 返却の集中（検索が同じ記憶に固着していないか）
    top_n, share = idle.top_share(len(active_ids))
    if top_n:
        lines.append(
            f"  返却集中度: 上位{top_n}件が全返却スロットの{share:.1%}を占有"
            + mark(_judge_low_is_good(share, _CONCENTRATION))
        )

    # C6: 判断を返しているか（時間で減衰しない設計なので、これが0だと何も沈まない）
    s = report.search_stats
    if s.total_searches:
        lines.append(
            f"  dismiss率: {s.dismiss_rate:.2%}（検索1回あたり）"
            + mark(_judge_high_is_good(s.dismiss_rate, _DISMISS_RATE))
        )
        lines.append(f"  boost率: {s.boost_rate_per_search:.2%}（検索1回あたり）")

    # mark_used捕捉率（CYCLE19.3の集計）。空振り常連の閾値Nはこの値から決まる
    lines.append(
        f"  mark_used捕捉率: {idle.capture_rate:.1%}"
        f"（{idle.used_count}/{idle.total_slots}スロット）"
    )

    # 空振り常連（CYCLE19.5）
    if idle.candidates:
        lines.append(
            f"  空振り常連: {len(idle.candidates)}件"
            f"（返却スロットの{idle.occupied_ratio:.1%}を占有・閾値は返却{idle.threshold}回以上）"
            "→ cycle_complete で候補を提示します"
        )
    elif idle.threshold is not None:
        lines.append(f"  空振り常連: 0件（閾値は返却{idle.threshold}回以上）")

    # archive候補（CYCLE19.4）
    lines.append(
        f"  archive候補: {len(archive_candidates)}件"
        f"（Priority {ARCHIVE_CANDIDATE_THRESHOLD} 以下＝検索から消えかけている記憶）"
    )

    # embeddingの出所（CYCLE19.2 の embedding_model 列）
    dist = report.embedding_model_distribution
    if dist:
        unknown = dist.get("", 0)
        known = {k: v for k, v in dist.items() if k}
        if not known:
            lines.append(f"  embedding: 全{unknown}件がモデル未記録（19.2以前に保存）")
        else:
            known_desc = " / ".join(f"{k}: {v}件" for k, v in sorted(known.items()))
            suffix = f" / 未記録: {unknown}件" if unknown else ""
            lines.append(f"  embedding: {known_desc}{suffix}")
            if len(known) > 1:
                # 複数モデルが混在＝保存済みとクエリが別空間になっている可能性
                lines.append(
                    "  ⚠ embeddingのモデルが混在しています。"
                    "memory_reembed で作り直すと検索精度が戻ることがあります"
                )

    return lines


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

    # CYCLE19.6（A4）: 記憶の読み込みは1回だけ。
    # collector・空振り常連・archive候補の3つが同じ全件読み込みを必要とするので、
    # ここで読んで配る（別々に読むとストアが育つほど3倍の時間がかかる）。
    memories = await system.persistence.async_load_all(include_archived=True)
    active_ids = {m.id for m in memories if not m.archived}

    collector = DiagnosticsCollector(event_logger, system.persistence)
    report = collector.collect(period_days=period_days, memories=memories)

    # CYCLE19.6: 19.4・19.5で作った集計を呼ぶだけにする（同じロジックを書き直さない）
    idle = await IdleRecallAnalyzer(event_logger, system.persistence).async_analyze(
        memories=memories
    )
    archive_candidates = await system.async_archive_candidates(memories=memories)
    org_enabled = bool(_server._config and _server._config.org_server_enabled)

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

    lines.append(f"\n--- 検索品質（直近{period_days}日） ---")
    s = report.search_stats
    lines.append(f"  検索回数: {s.total_searches}")
    lines.append(f"  平均スコア: {s.avg_score:.1f}")
    lines.append(f"  boost: {s.boost_count}回 / dismiss: {s.dismiss_count}回")
    lines.append(f"  フィードバックのうちboost: {s.boost_rate:.0%}")

    lines.extend(_health_lines(report, idle, archive_candidates, active_ids))

    # 昇格はEnterprise（Org Layer）だけの機能。
    # CYCLE19.6（A4）: Core構成で「昇格回数: 0」を出すのは、
    # 存在しない機能の数字を見せることになる（A1と同種のずれ）。
    if org_enabled:
        lines.append("\n--- 昇格統計 ---")
        p = report.promotion_stats
        lines.append(f"  昇格回数: {p.total_promotions}")
        lines.append(f"  昇格後の平均参照回数: {p.avg_post_promotion_access:.1f}")

    lines.append("\n--- 3指標（証明装置） ---")
    pr = report.precision_stats
    lines.append(f"  Memory Precision: {pr.precision_rate:.0%}（{pr.used_count}/{pr.total_recalled}）")
    lines.append(f"  フィードバックのうちboost: {s.boost_rate:.0%}")
    lines.append(f"  平均検索スコア: {s.avg_score:.1f}")

    # セッション別Precision（CYCLE13.2 FR031 P1）
    if pr.session_count > 0:
        lines.append(
            f"  セッション別Precision: {pr.avg_session_precision:.0%}"
            f"（{pr.session_count}セッション平均）"
        )
    # mark_usedの経路別内訳（CYCLE19.3 FR035 方向3）
    if pr.recall_used_by_source:
        breakdown = " / ".join(
            f"{src}: {n}件"
            for src, n in sorted(pr.recall_used_by_source.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"  mark_usedの経路: {breakdown}")

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
    # CYCLE19.2 (A8): embeddingと出所は必ず同時に書く。
    # 片方だけ書くと「新しいembedding × 記録なし/古い記録」になり、
    # 次にモデルが変わったときこの行だけ検知をすり抜ける。
    saved = 0
    for memory, embedding in zip(targets, embeddings):
        success = await system.persistence.async_update(memory.id, {
            "embedding": embedding,
            "embedding_model": emb_mgr.model_id,
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
