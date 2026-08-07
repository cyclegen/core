"""test_doc_drift.py — 仕様と実装のずれの再発防止（CYCLE19.1 / A1）

背景（CYCLE19の実測）:
配布物の中に、実装と食い違う数値・説明が10箇所あった。
そのうち3箇所は「AIが読む文字列」（MCPツールの説明文・cycle_completeの出力・
3d-eval評価基準の提示文）で、AIはその誤った数値を前提に判断していた。

コメントは機構ではない（CYCLE17.3 F2）。同じことを注意書きで防ごうとしても
また同じずれが入るので、テストで落とす。

ここで見ているのは「実装と説明の一致」ではなく「既に直したずれが戻っていないこと」。
実装側の値を変えるときは、このテストの期待値も一緒に変わるべきである。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cyclegen.core.priority import PriorityManager, _DEFAULT_PRIORITY

SRC = Path(__file__).resolve().parents[2] / "src" / "cyclegen"

# 拡張子を絞らないのは、3d_eval_default.yaml のようなデータファイルにも
# 同じ説明文が入っていたため（CYCLE19.1で実際に見つかった）。
_SCANNED_SUFFIXES = {".py", ".yaml", ".yml", ".md"}


def _iter_sources():
    for p in sorted(SRC.rglob("*")):
        if p.is_file() and p.suffix in _SCANNED_SUFFIXES:
            yield p


def _hits(needle: str) -> list[str]:
    out = []
    for p in _iter_sources():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                out.append(f"{p.relative_to(SRC)}:{i}: {line.strip()}")
    return out


class TestPriorityDocsMatchImplementation:
    def test_initial_priority_is_documented_as_05(self):
        """初期Priorityは0.5。'0.3固定' という記述が残っていないこと。

        CYCLE12.7で0.3→0.5に変えたが、説明文が5箇所取り残されていた。
        うち2箇所（cycle_completeの出力・3d-eval提示文）はAIが読む。
        """
        assert _DEFAULT_PRIORITY == 0.5
        hits = _hits("0.3固定")
        assert hits == [], "初期Priorityは0.5。古い『0.3固定』が残っている:\n" + "\n".join(hits)

    def test_boost_delta_is_documented_as_010(self):
        """boostは+0.10。'+0.15' という記述が残っていないこと。

        memory_boost のMCPツール説明文が+0.15と書いており、
        AIはこの数値でPriorityの動きを見積もっていた。
        """
        assert PriorityManager().apply_boost(0.5) == pytest.approx(0.6)
        hits = _hits("+0.15")
        assert hits == [], "boostは+0.10。古い『+0.15』が残っている:\n" + "\n".join(hits)

    def test_no_claim_of_priority_decay(self):
        """鮮度減衰は存在しない。'Priority減衰' を約束する記述が無いこと。

        時間による自動減衰はCYCLE12.7.4で廃止済み（記憶の庭師が担当する方針）。
        pinの応答が「Priority減衰停止」と答えていたが、止める対象が無い。
        判断による減衰（dismiss）は別物であり、この検査の対象ではない。
        """
        hits = _hits("Priority減衰")
        assert hits == [], "鮮度減衰は実装に存在しない:\n" + "\n".join(hits)


class TestDismissStillLowersPriority:
    def test_dismiss_is_the_surviving_downward_path(self):
        """判断による減衰（dismiss）は生きている。

        『減衰が無い』と一括りにしないための固定。廃止されたのは時間側だけで、
        dismissは実装されている（CYCLE19の訂正）。
        """
        pm = PriorityManager()
        assert pm.apply_dismiss(0.5) == pytest.approx(0.4)
