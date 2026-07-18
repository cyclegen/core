"""mcp/tools/memory.py — 記憶操作ツール8本

実装計画書§7.2: store/search/update/delete/pin/archive/boost/dismiss
CYCLE7.7.3.1: async化（system.async_xxx + event_logger.async_log）
CYCLE8.4: SaaS guard追加（SaaSモード外ではno-op）
"""

from __future__ import annotations

from typing import Optional

from cyclegen.config import format_3d_eval_prompt, load_3d_eval
from cyclegen.mcp.server import _async_get_system, _get_config, mcp
from cyclegen.models import EventType, SearchResult


def _format_search_result(index: int, r: SearchResult) -> str:
    """検索結果1件のフォーマット（2チャネル共通）。"""
    preview = (
        r.memory.content[:150] + "..."
        if len(r.memory.content) > 150
        else r.memory.content
    )
    meta_parts = []
    if r.memory.tags:
        meta_parts.append(f"タグ: {', '.join(r.memory.tags)}")
    if r.memory.pinned:
        meta_parts.append("📌ピン留め")
    if r.memory.agent_id:
        meta_parts.append(f"agent: {r.memory.agent_id}")
    meta_line = f"   {' | '.join(meta_parts)}\n" if meta_parts else ""
    return (
        f"{index}. [{r.source}] L{r.memory.coordinates.layer}/P{r.memory.coordinates.priority:.2f}/C:{r.memory.coordinates.context}  "
        f"スコア:{r.score:.0f}\n"
        f"   {preview}\n"
        f"{meta_line}"
        f"   理由: {r.reason}\n"
        f"   ID: {r.memory.id}"
    )


@mcp.tool()
async def memory_store(
    content: str,
    layer: Optional[int] = None,
    context: Optional[str] = None,
    tags: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """3次元記憶に記憶を保存する。

    layer/context を指定して呼ぶこと。Priorityは0.5固定（利用実績で動的に変動）。
    layer が省略された場合は評価基準（3d-eval）が返されるので、
    基準に従って判定し、再度呼び直すこと。

    Args:
        content: 記憶する内容（Markdownテキスト）
        layer: レイヤー番号（1-5）。省略すると評価基準が返される
        context: コンテキスト（定義済み9種: planning, implementation, debugging,
                 review, learning, documentation, operations, research, strategy）。
                 省略するとembedding類似度で自動判定される。
                 未定義のContextを指定した場合は警告を返し自動判定に切り替わる。
        tags: タグ（カンマ区切り。例: "認証,Keycloak"）
        agent_id: エージェントID（マルチエージェント環境用、省略可）
    """
    # Layer省略 → 3d-eval フィードバック（FR004）
    # Context省略は自動判定に委ねるため3d-evalにしない（CYCLE12.8.2 FR023）
    if layer is None:
        config = _get_config()
        eval_criteria = load_3d_eval(config)
        return format_3d_eval_prompt(eval_criteria, content)

    system, _, event_logger = await _async_get_system()

    # CYCLE12.8.2 FR023: 未定義Contextの事前チェック（警告メッセージ生成用）
    original_context = context
    context_warning = ""
    if context is not None and not system.context_selector.validate(context):
        context_warning = f"\n  ⚠ Context '{context}' は未定義のため自動判定に切り替えました"

    # SaaS guard（Quota + レート制限。SaaSモード外ではno-op）
    from cyclegen.saas.guard import guard_store
    await guard_store(system.persistence)

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    memory = await system.async_store(
        content=content, layer=layer, context=context,
        tags=tag_list, agent_id=agent_id,
    )
    await event_logger.async_log(
        EventType.STORE,
        memory.id,
        {
            "layer": memory.coordinates.layer,
            "priority": memory.coordinates.priority,
            "context": memory.coordinates.context,
        },
    )

    # 未定義Contextが自動補正された場合は補正後のContextを警告に追記
    if context_warning:
        context_warning = f"\n  ⚠ Context '{original_context}' は未定義のため '{memory.coordinates.context}' に自動補正しました"

    return (
        f"記憶保存完了\n"
        f"  ID: {memory.id}\n"
        f"  座標: L{memory.coordinates.layer}/P{memory.coordinates.priority:.2f}/C:{memory.coordinates.context}\n"
        f"  ファイル: {memory.id}.md"
        f"{context_warning}"
    )


@mcp.tool()
async def memory_search(
    query: str,
    context: Optional[str] = None,
    layer_filter: Optional[str] = None,
    priority_threshold: float = 0.0,
    max_items: int = 7,
) -> str:
    """記憶を検索する（Personal + Org統合、Miller's 7±2準拠）。

    結果には各記憶の source（personal/org）と
    relevance.reason（なぜこの記憶が返されたか）が含まれる。
    利用者に結果の根拠を説明する際に reason を活用すること。

    ★作業開始時には必ず呼ぶこと。

    Args:
        query: 検索クエリ（自然言語テキスト）
        context: コンテキストフィルタ
        layer_filter: レイヤーフィルタ（カンマ区切り、例: "1,2,3"）
        priority_threshold: 最低優先度閾値（0.0-1.0）
        max_items: 最大取得件数（デフォルト7、Miller's法則）
    """
    # SaaS guard（レート制限。SaaSモード外ではno-op）
    from cyclegen.saas.guard import guard_search
    await guard_search()

    system, valve, event_logger = await _async_get_system()
    layers = [int(l.strip()) for l in layer_filter.split(",")] if layer_filter else None
    all_memories = await system.persistence.async_load_all()
    response = await valve.async_search(
        query=query,
        personal_memories=all_memories,
        context=context,
        layer_filter=layers,
        priority_threshold=priority_threshold,
        max_items=max_items,
    )
    # アクセス記録（タスク+メタ両方）
    all_results = response.memories + response.meta_memories
    for r in all_results:
        await system.async_record_access(r.memory.id)
    # CYCLE13.2 FR031 P1: search→recall_usedを紐付けるsession_idを付与
    from cyclegen.mcp.session import get_session_id
    await event_logger.async_log(
        EventType.SEARCH,
        details={
            "session_id": get_session_id(),
            "query": query,
            "results_count": len(response.memories),
            "meta_count": len(response.meta_memories),
            "top_score": response.memories[0].score if response.memories else 0,
            "recalled_ids": [r.memory.id for r in all_results],
        },
    )
    # 出力整形（CYCLE12.8.4: 2チャネル表示）
    total = len(response.memories) + len(response.meta_memories)
    if total == 0:
        return f"検索結果: 0件（クエリ: {query}）"

    # ヘッダー
    if response.meta_memories:
        header = (
            f"検索結果: {total}件"
            f"（メタ認知: {len(response.meta_memories)}件 + タスク: {len(response.memories)}件"
            f"、{response.search_time_ms:.0f}ms）"
        )
    else:
        header = f"検索結果: {len(response.memories)}件（クエリ: {query}、{response.search_time_ms:.0f}ms）"
    lines = [header, "---"]

    # メタ認知チャネル（L5、Miller's 7の外）
    if response.meta_memories:
        lines.append("")
        lines.append("[メタ認知チャネル — Miller's 7の外]")
        for i, r in enumerate(response.meta_memories, 1):
            lines.append(_format_search_result(i, r))

    # タスクチャネル（L1-4、Miller's 7±2）
    if response.memories:
        if response.meta_memories:
            lines.append("")
            lines.append("[タスクチャネル — Miller's 7±2]")
        for i, r in enumerate(response.memories, 1):
            lines.append(_format_search_result(i, r))

    lines.append("---")
    lines.append(
        "💡 この結果が役立ったら memory_boost(memory_id)、不適切なら memory_dismiss(memory_id) を呼んでください。\n"
        "   実際に参照・活用した記憶には memory_mark_used(memory_id) を呼んでください。"
    )
    return "\n".join(lines)


@mcp.tool()
async def memory_update(
    memory_id: str,
    content: Optional[str] = None,
    layer: Optional[int] = None,
    priority: Optional[float] = None,
    context: Optional[str] = None,
) -> str:
    """記憶を更新する。指定したフィールドのみ更新される。

    Args:
        memory_id: 更新対象の記憶ID
        content: 新しい内容（省略時は変更なし）
        layer: 新しいレイヤー（省略時は変更なし）
        priority: 新しい優先度（省略時は変更なし）
        context: 新しいコンテキスト（省略時は変更なし）
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    updates = {}
    if content is not None:
        updates["content"] = content
    if layer is not None:
        updates["coordinates.layer"] = layer
    if priority is not None:
        updates["coordinates.priority"] = priority
    if context is not None:
        updates["coordinates.context"] = context
    result = await system.async_update(memory_id, updates)
    if result is None:
        return f"エラー: ID '{memory_id}' が見つかりません"
    await event_logger.async_log(EventType.UPDATE, memory_id, updates)
    return f"更新完了: {memory_id}"


@mcp.tool()
async def memory_delete(memory_id: str) -> str:
    """記憶を削除する。mdファイルとSQLiteインデックスの両方から削除される。

    Args:
        memory_id: 削除対象の記憶ID
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    if await system.async_delete(memory_id):
        await event_logger.async_log(EventType.DELETE, memory_id)
        return f"削除完了: {memory_id}"
    return f"エラー: ID '{memory_id}' が見つかりません"


@mcp.tool()
async def memory_pin(memory_id: str) -> str:
    """記憶を重要マークする。Priority が時間減衰しなくなる。

    ★利用者が「重要」「忘れないで」と言った時に呼ぶこと。

    Args:
        memory_id: 対象の記憶ID
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    result = await system.async_pin(memory_id)
    if result is None:
        return f"エラー: ID '{memory_id}' が見つかりません"
    await event_logger.async_log(EventType.PIN, memory_id)
    return f"ピン留め完了: {memory_id}（Priority減衰停止）"


@mcp.tool()
async def memory_archive(memory_id: str) -> str:
    """記憶をアーカイブする。通常検索から除外される（明示検索で復帰可能）。

    ★利用者が「いらない」「もう使わない」と言った時に呼ぶこと。

    Args:
        memory_id: 対象の記憶ID
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    result = await system.async_archive(memory_id)
    if result is None:
        return f"エラー: ID '{memory_id}' が見つかりません"
    await event_logger.async_log(EventType.ARCHIVE, memory_id)
    return f"アーカイブ完了: {memory_id}（検索除外）"


@mcp.tool()
async def memory_unarchive(memory_id: str) -> str:
    """アーカイブを解除する。通常検索に復帰する。

    ★利用者が「やっぱり使う」「戻して」と言った時に呼ぶこと。

    Args:
        memory_id: 対象の記憶ID
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    result = await system.async_unarchive(memory_id)
    if result is None:
        return f"エラー: ID '{memory_id}' が見つかりません"
    await event_logger.async_log(EventType.UPDATE, memory_id, {"action": "unarchive"})
    return f"アーカイブ解除完了: {memory_id}（検索復帰）"


@mcp.tool()
async def memory_boost(memory_id: str) -> str:
    """検索結果が役立った時のフィードバック。Priority +0.15。

    ★利用者が「役立った」「それそれ」と言った時に呼ぶこと。

    Args:
        memory_id: 対象の記憶ID
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    result = await system.async_boost(memory_id)
    if result is None:
        return f"エラー: ID '{memory_id}' が見つかりません"
    await event_logger.async_log(
        EventType.BOOST, memory_id, {"new_priority": result.coordinates.priority}
    )
    return f"boost完了: {memory_id}（Priority → {result.coordinates.priority:.2f}）"


@mcp.tool()
async def memory_dismiss(memory_id: str) -> str:
    """検索結果が不適切だった時のフィードバック。Priority -0.10。

    ★利用者が「違う」「関係ない」と言った時に呼ぶこと。

    Args:
        memory_id: 対象の記憶ID
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    result = await system.async_dismiss(memory_id)
    if result is None:
        return f"エラー: ID '{memory_id}' が見つかりません"
    await event_logger.async_log(
        EventType.DISMISS, memory_id, {"new_priority": result.coordinates.priority}
    )
    return f"dismiss完了: {memory_id}（Priority → {result.coordinates.priority:.2f}）"


@mcp.tool()
async def memory_mark_used(memory_id: str) -> str:
    """検索で返された記憶を実際にCYCLE内で使った時の記録。Memory Precision測定用。

    memory_searchで返された記憶のうち、実際に判断・作業に活用した記憶に対して呼ぶ。
    この記録がMemory Precision（返却された記憶のうち実際に使われた割合）の測定データになる。

    ★検索結果の記憶を参照して作業した時に呼ぶこと。

    Args:
        memory_id: 実際に使用した記憶のID
    """
    from cyclegen.saas.guard import guard_general
    await guard_general()

    system, _, event_logger = await _async_get_system()
    memory = await system.persistence.async_load(memory_id)
    if memory is None:
        return f"エラー: ID '{memory_id}' が見つかりません"

    # Priority +0.05 増進（CYCLE12: 利用証明）
    new_priority = system.priority_manager.apply_mark_used_boost(memory.coordinates.priority)
    await system.persistence.async_update(memory_id, {
        "coordinates.priority": new_priority,
    })

    # CYCLE13.2 FR031 P1: 同一セッションのsearchと紐付けるsession_idを付与
    from cyclegen.mcp.session import get_session_id
    await event_logger.async_log(
        EventType.RECALL_USED, memory_id, {"session_id": get_session_id()}
    )
    return f"利用記録完了: {memory_id}（Priority → {new_priority:.2f}、Memory Precision測定に反映）"
