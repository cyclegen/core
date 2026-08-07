"""CycleGen Enterprise データモデル定義

設計書v1.1 §6 / 実装計画書§2 に基づくPydanticモデル全定義。
3次元記憶システムの座標系（Layer × Priority × Context）を型安全に表現する。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

import uuid

from pydantic import BaseModel, Field


# === 列挙型 ===


class LayerKey(str, Enum):
    """5層抽象度軸（§8 #1 確定: strategy）"""

    METACOGNITION = "metacognition"  # Layer 5
    STRATEGY = "strategy"  # Layer 4
    EXPERTISE = "expertise"  # Layer 3
    IMPLEMENTATION = "implementation"  # Layer 2
    FOUNDATION = "foundation"  # Layer 1


class StorageTier(str, Enum):
    """ストレージ階層（IP-009: Priority連動）"""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class PromotionReason(str, Enum):
    """昇格理由"""

    CYCLE_COMPLETE = "cycle_complete"
    MANUAL = "manual"
    APPROVED = "approved"  # CYCLE10.2: HITL承認フローで承認


class EventType(str, Enum):
    """イベントログ種別"""

    STORE = "store"
    SEARCH = "search"
    UPDATE = "update"
    DELETE = "delete"
    PIN = "pin"
    ARCHIVE = "archive"
    BOOST = "boost"
    DISMISS = "dismiss"
    PROMOTE = "promote"
    DECAY = "decay"
    RECALL_USED = "recall_used"  # CYCLE6.2: 検索結果が実際に使われた記録
    PROMOTION_SUGGESTED = "promotion_suggested"  # CYCLE10.2: 昇格候補として提示
    PROMOTION_REJECTED = "promotion_rejected"  # CYCLE10.2: 昇格候補を却下


# === 3次元座標 ===


class Coordinates(BaseModel):
    """3次元記憶座標: Layer(1-5) × Priority(0.0-1.0) × Context(文字列)

    CYCLE12.7: Priority初期値0.5固定。利用実績で動的変動。
    """

    layer: int = Field(ge=1, le=5)
    priority: float = Field(default=0.3, ge=0.0, le=1.0)
    context: str


# === 記憶オブジェクト ===


def compute_content_hash(content: str) -> str:
    """本文から content_hash（SHA-256）を計算する。

    CYCLE20.5（FR061⓪）: 保存時と更新時で計算方法がずれないよう、
    唯一の計算箇所をここに置く。content_hash は「本文の指紋」であり、
    **本文が変わったら必ず一緒に変わる**というのがこの値の意味である。
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Memory(BaseModel):
    """Personal Layer / Org Layer 共通の記憶オブジェクト"""

    id: str = Field(
        default_factory=lambda: f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    )
    content: str
    coordinates: Coordinates
    tags: list[str] = Field(default_factory=list)
    owner_id: str = ""
    agent_id: Optional[str] = None  # CYCLE6.1: マルチエージェント対応（オプショナル）
    content_hash: str = ""  # CYCLE7.2.2: SHA-256（重複検知用）
    pinned: bool = False
    archived: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_accessed_at: datetime = Field(default_factory=datetime.now)
    embedding: Optional[bytes] = None  # CYCLE12.7.1: セマンティック検索用ベクトル（float32 BLOB）
    # CYCLE19.2: このembeddingを作ったモデルの識別子（例 "...MiniLM-L12-v2@fastembed0.8.0"）。
    # None = 出所が記録されていない（A8導入前に保存された記憶）。
    # 「不明」と「一致している」を区別できるようにするため、推測で埋めてはならない。
    embedding_model: Optional[str] = None
    access_count: int = 0
    score_version: int = 1  # CYCLE12.6: Priorityロジックバージョン（v1=旧宣言的, v2=利用増進）
    version: int = 1


class OrgMemory(Memory):
    """Org Layer 固有のフィールドを追加"""

    promoted_at: Optional[datetime] = None
    promoted_by: Optional[str] = None
    promotion_reason: Optional[str] = None
    storage_tier: StorageTier = StorageTier.HOT
    metadata: dict = Field(default_factory=dict)


# === 検索 ===


class SearchResult(BaseModel):
    """検索結果1件"""

    memory: Memory
    score: float = Field(ge=0, le=100)
    source: str  # "personal" | "org"
    reason: str  # 人間可読な理由文


class SearchResponse(BaseModel):
    """検索レスポンス"""

    memories: list[SearchResult]
    total_candidates: int
    search_time_ms: float
    meta_memories: list[SearchResult] = Field(
        default_factory=list,
        description="メタ認知チャネル（L5）の検索結果。Miller's 7の外で管理。",
    )


# === 昇格 ===


class PromotionRequest(BaseModel):
    """Personal → Org 昇格リクエスト"""

    memory_id: str
    content: str
    coordinates: Coordinates
    tags: list[str] = Field(default_factory=list)
    owner_id: str
    reason: str


class PromotionCriteria(BaseModel):
    """昇格基準（設計書v2 §1.3 原則5 + IP-033）

    cycle_complete時にPersonal記憶をスキャンし、
    この基準を満たす記憶のみOrg Layerに選択的昇格する。
    """

    priority_min: float = Field(default=0.7, description="最低Priority閾値（0.7=重要な発見・確定方針）")
    layer_min: int = Field(default=3, description="最低Layer閾値（L3以上=パターン・原則）")
    access_count_min: int = Field(default=2, description="最低参照回数（1回のみは昇格しない）")
    pinned_auto_promote: bool = Field(default=True, description="pinned=trueなら無条件昇格")
    exclude_tag_prefixes: list[str] = Field(
        default_factory=lambda: ["import:"],
        description="このプレフィックスを持つタグがある記憶は昇格しない",
    )

    def is_promotable(self, memory: "Memory") -> bool:
        """記憶が昇格基準を満たすかどうかを判定する。"""
        # 除外タグチェック
        for tag in memory.tags:
            for prefix in self.exclude_tag_prefixes:
                if tag.startswith(prefix):
                    return False

        # pinned は無条件昇格
        if self.pinned_auto_promote and memory.pinned:
            return True

        # 3条件すべてを満たす必要がある
        return (
            memory.coordinates.priority >= self.priority_min
            and memory.coordinates.layer >= self.layer_min
            and memory.access_count >= self.access_count_min
        )


class PromotionLog(BaseModel):
    """昇格ログ"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    memory_id: str
    promoted_by: str
    reason: str
    promoted_at: datetime = Field(default_factory=datetime.now)


# === モニタリング ===


class EventLogEntry(BaseModel):
    """イベントログエントリ"""

    event_type: EventType
    memory_id: Optional[str] = None
    details: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


class SearchStats(BaseModel):
    """検索統計"""

    total_searches: int
    avg_score: float
    boost_count: int
    dismiss_count: int
    boost_rate: float  # boost / (boost + dismiss)＝フィードバックのうち肯定の割合

    # CYCLE19.6（A4）: 分母が検索回数のシグナル率。
    # boost_rate とは分母が違う（あちらはフィードバック総数）。
    # 「どれくらい判断を返しているか」を見るので、こちらは検索回数で割る。
    dismiss_rate: float = 0.0  # dismiss / 検索回数
    boost_rate_per_search: float = 0.0  # boost / 検索回数


class PromotionStats(BaseModel):
    """昇格統計"""

    total_promotions: int
    avg_post_promotion_access: float  # 昇格後の平均access_count


class PrecisionStats(BaseModel):
    """Memory Precision統計（CYCLE6.2: 証明装置としての定量指標）"""

    total_recalled: int  # recallされた記憶の延べ数
    used_count: int  # 実際に使われた記憶数（mark_used回数）
    precision_rate: float  # used_count / total_recalled（0.0-1.0）

    # CYCLE13.2 FR031 P1: session_idによるセッション別Precision
    session_count: int = 0  # session_id付き検索を含むセッション数
    avg_session_precision: float = 0.0  # セッション単位Precisionの平均（0.0-1.0）
    session_precision: dict[str, float] = Field(default_factory=dict)  # session_id -> precision

    # CYCLE19.3 FR035 方向3: mark_usedがどの経路で記録されたかの内訳
    # 経路ごとに信頼度が違うので、混ぜたまま1つの数字にしない。
    #   "explicit"        : 利用者/AIが memory_mark_used を直接呼んだ
    #   "cycle_complete_explicit": cycle_complete の used_memory_ids で渡された
    #   "cycle_complete_auto"    : summary本文のID文字列をregex検出（弱い）
    #   "cycle_complete_inferred": embedding照合による推定（FR035 方向4・未実装）
    # 推定を足すと捕捉率が実態より高く見え、そこから導く閾値が甘くなるため、
    # 「明示のみ」を常に取り出せる形で持つ。
    recall_used_by_source: dict[str, int] = Field(default_factory=dict)


class DiagnosticsReport(BaseModel):
    """memory_diagnostics ツールの出力"""

    total_memories: int
    layer_distribution: dict[int, int]  # {1: 5, 2: 10, ...}
    priority_distribution: dict[str, int]  # {"high": 8, "medium": 15, ...}
    priority_detail: dict[str, int] = Field(default_factory=dict)  # CYCLE12: {"0.3": 50, "0.4": 10, ...}
    context_distribution: dict[str, int]  # {"planning": 5, ...}
    pinned_count: int
    archived_count: int
    access_count_zero: int = 0  # CYCLE12: access_count=0の件数
    search_stats: SearchStats
    promotion_stats: PromotionStats
    precision_stats: PrecisionStats  # CYCLE6.2: Memory Precision

    # CYCLE19.6（A4）: embeddingの出所の内訳（CYCLE19.2 の embedding_model 列）。
    # キーは model_id、未記録は "" で数える。
    # 「不明」と「一致」を区別するのが目的なので、未記録をまとめて0扱いにしない。
    embedding_model_distribution: dict[str, int] = Field(default_factory=dict)

    warnings: list[str] = Field(default_factory=list)  # CYCLE12: 偏り警告


# === 設定 ===


class ScoringWeights(BaseModel):
    """検索スコアリングの重み配分（100点満点）"""

    keyword_frequency: int = 40
    exact_match: int = 30
    priority: int = 20
    access_count: int = 10


class ContextDefinition(BaseModel):
    """enterprise_contexts.yaml の1Context定義"""

    weight: float
    keywords: list[str]
    layer_priority: list[int]  # Layer検索優先順


class MemorySourceConfig(BaseModel):
    """memory_sources セクションの1エントリ（設計書v2 §1.3）"""

    name: str  # "personal", "org", "team" 等
    backend: str  # "local" | "postgresql" | "cloud"
    owner_id: str = ""  # テナント分離用（空文字=共有）
    dsn: str = ""  # postgresql 用
    table: str = ""  # postgresql 用テーブル名
    url: str = ""  # cloud 用（Org Server URL）
    api_key: str = ""  # cloud 用


class ValveConfig(BaseModel):
    """統合検索バルブの設定（設計書v2 §1.3）"""

    local_bonus: int = 20  # ローカルソースへのスコア加算（旧personal_bonus）
    source_min_slots: dict[str, int] = Field(
        default_factory=lambda: {"org": 2},
        description="ソースごとの最低保証枠（旧org_min_slots）",
    )
    meta_max_items: int = 3  # CYCLE12.8.3: メタ認知チャネル（L5）の最大件数


class CycleGenConfig(BaseModel):
    """cyclegen_config.yaml のルート設定"""

    home: str = "~/.cyclegen"
    personal_db: str = "index.db"
    # --- 旧設定（後方互換） ---
    org_server_enabled: bool = False
    org_server_url: str = ""
    org_api_key: str = ""
    org_timeout_ms: int = 3000
    default_max_items: int = 7
    personal_bonus: int = 20
    org_min_slots: int = 2  # FR012: Org結果の最低保証枠
    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)
    contexts_file: str = "enterprise_contexts.yaml"
    # --- 新設定（Memory Source Resolver） ---
    memory_sources: list[MemorySourceConfig] = Field(default_factory=list)
    valve: ValveConfig = Field(default_factory=ValveConfig)
