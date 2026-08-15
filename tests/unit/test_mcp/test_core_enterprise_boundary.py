"""test_core_enterprise_boundary.py — Core/Enterprise境界の検算（CYCLE20.6）

CYCLE20.6で、母艦の `mcp/tools/lifecycle.py` を公開Coreへ丸ごとコピーしかけた。
このファイルは **Core と Enterprise が同居している唯一のファイル**で、
コピーすると昇格3ツールが公開Coreに出荷される＝**オープンコアの商用境界が消える**。

そのとき止めるものが何も無かった。同じ事故を起こしたテスト2ファイルは
「落ちた」ので気づけたが、`lifecycle.py` は**全テストが通ったまま境界だけが壊れる**。

> 境界は `org/` `paas/` `remote/` のようなディレクトリだけでは表現しきれない。
> **同居しているファイルには、機械が数えられる不変量を置く。**

このテストは母艦と公開Coreの両方で同じものが動く:
  - 公開Core（Enterprise層なし）: 登録ツールは CORE_TOOLS ちょうど
  - 母艦（Enterprise層あり）  : CORE_TOOLS ＋ ENTERPRISE_TOOLS ちょうど

どちらの向きにも効く——Coreに昇格ツールが混入しても、
Coreのツールが**Enterpriseにだけ**足されて配布物から漏れても落ちる。
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil

import pytest

# 公開Coreが提供する19本（CYCLE15.0で確定した Core/Enterprise 境界）＝
# 常時17本 ＋ docx extra の2本。
CORE_TOOLS = {
    "memory_store",
    "memory_search",
    "memory_update",
    "memory_delete",
    "memory_pin",
    "memory_archive",
    "memory_unarchive",
    "memory_boost",
    "memory_dismiss",
    "memory_mark_used",
    "memory_bulk_import",
    "memory_recalculate",
    "memory_reclassify",
    "memory_reembed",
    "memory_status",
    "memory_diagnostics",
    "cycle_complete",
}

# `cyclegen[docx]` を入れたときだけ登録される2本（`register_finish_tools`）。
# 条件つき登録も境界の一種なので、条件ごと書いておく
# ——「入っていない」と「壊れている」を取り違えないため。
FINISH_TOOLS = {
    "document_finish",
    "list_finish_templates",
}

# Enterprise専用の3本（組織昇格）。公開Coreには同梱しない。
ENTERPRISE_TOOLS = {
    "memory_promote",
    "promotion_approve",
    "promotion_reject",
}

HAS_ENTERPRISE = importlib.util.find_spec("cyclegen.org") is not None
HAS_DOCX = importlib.util.find_spec("docx") is not None


async def _registered_tool_names() -> set[str]:
    """ツール定義モジュールを全部読み込んでから、登録済みツール名を返す。

    モジュールの import が登録の副作用なので、数える前に必ず全部読む
    （読み漏らすと「少ないこと」を検出できない）。
    """
    import cyclegen.mcp.tools as tools_pkg

    for module in pkgutil.iter_modules(tools_pkg.__path__):
        importlib.import_module(f"cyclegen.mcp.tools.{module.name}")

    # finish ツールは import ではなく明示登録（docx extra 依存）
    from cyclegen.mcp.tools.finish import register_finish_tools
    from cyclegen.mcp.server import mcp

    register_finish_tools(mcp)
    return {t.name for t in await mcp.list_tools()}


async def test_tool_set_matches_the_declared_boundary():
    expected = set(CORE_TOOLS)
    if HAS_DOCX:
        expected |= FINISH_TOOLS
    if HAS_ENTERPRISE:
        expected |= ENTERPRISE_TOOLS
    actual = await _registered_tool_names()

    assert actual == expected, (
        f"ツールの集合が宣言と食い違う。\n"
        f"  余分: {sorted(actual - expected)}\n"
        f"  不足: {sorted(expected - actual)}\n"
        f"  Enterprise層: {'あり（母艦）' if HAS_ENTERPRISE else 'なし（公開Core）'}"
        f" / docx extra: {'あり' if HAS_DOCX else 'なし'}\n"
        f"ツールを足したなら、このテストの CORE_TOOLS / ENTERPRISE_TOOLS も更新すること。"
    )


@pytest.mark.skipif(HAS_ENTERPRISE, reason="公開Coreのチェックアウトでのみ意味を持つ")
async def test_public_core_does_not_ship_promotion_tools():
    """公開Coreに昇格ツールが混ざっていないこと（丸ごとコピーの検出）。"""
    actual = await _registered_tool_names()
    assert not (actual & ENTERPRISE_TOOLS), (
        f"Enterprise専用ツールが公開Coreに混入している: {sorted(actual & ENTERPRISE_TOOLS)}。"
        f"`mcp/tools/lifecycle.py` を丸ごとコピーした可能性が高い（CYCLE20.6）"
    )


async def test_core_tool_descriptions_do_not_name_enterprise_tools():
    """★Coreツールの説明文が、Coreに無いツールの名前を出さないこと（CYCLE20.7 / F-19）。

    20.6 で守ったのは**コードの境界**だった。実機（WIN-01・面B）で漏れたのは
    **説明文のほう**——`cycle_complete` の docstring が
    「承認: promotion_approve(memory_id) / 却下: promotion_reject(memory_id)」と
    案内しており、この2本は公開Coreの19ツールに存在しない。

    docstring は MCP のスキーマとして**そのまま利用者とAIに渡る**。
    だから説明文は飾りではなく、配布物の一部である。
    ★上のツール集合テストは、この漏れを検出できない（ツールの数は正しいから）。

    > 境界はファイルの中を通る（FR065）。ファイルの中の、**文字列の中まで**通っている。
    """
    from cyclegen.mcp.server import mcp

    await _registered_tool_names()  # 登録の副作用を先に済ませる

    offenders = []
    for tool in await mcp.list_tools():
        if tool.name in ENTERPRISE_TOOLS:
            continue  # Enterprise ツール自身が自分の名前を書くのは正しい
        for name in ENTERPRISE_TOOLS:
            if name in (tool.description or ""):
                offenders.append(f"{tool.name} の説明文が {name} を案内している")

    assert offenders == [], (
        "Coreツールの説明文が Enterprise専用ツールを案内している:\n  "
        + "\n  ".join(offenders)
        + "\n利用者とAIは、存在しないツールを呼ぼうとする（CYCLE17.6 / F-19）。"
    )


@pytest.mark.skipif(not HAS_ENTERPRISE, reason="母艦のチェックアウトでのみ意味を持つ")
async def test_mothership_has_exactly_three_more():
    """母艦と公開Coreの差は、ちょうど昇格3本であること。

    差が3本から動いたなら、Core/Enterprise境界の再確認が要る
    （15.0の「Core 19／Enterprise 3」が変わったということ）。
    """
    actual = await _registered_tool_names()
    assert actual - CORE_TOOLS - FINISH_TOOLS == ENTERPRISE_TOOLS
