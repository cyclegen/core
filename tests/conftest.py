"""共通テストフィクスチャ"""

from __future__ import annotations

import pytest

from cyclegen.models import (
    ContextDefinition,
    Coordinates,
    CycleGenConfig,
    Memory,
    ScoringWeights,
)
from cyclegen.core.layer import LayerHierarchy
from cyclegen.core.priority import PriorityManager
from cyclegen.core.context import ContextSelector
from cyclegen.core.classifier import AutoLayerClassifier


@pytest.fixture
def sample_coordinates() -> Coordinates:
    return Coordinates(layer=3, priority=0.7, context="implementation")


@pytest.fixture
def sample_memory(sample_coordinates: Coordinates) -> Memory:
    return Memory(
        content="Pythonでデータモデルを実装した",
        coordinates=sample_coordinates,
        tags=["python", "model"],
        owner_id="user1",
    )


@pytest.fixture
def default_config() -> CycleGenConfig:
    return CycleGenConfig()


@pytest.fixture
def layer_hierarchy() -> LayerHierarchy:
    return LayerHierarchy()


@pytest.fixture
def priority_manager() -> PriorityManager:
    return PriorityManager()


@pytest.fixture
def default_contexts() -> dict[str, ContextDefinition]:
    from cyclegen.config import DEFAULT_CONTEXTS
    return {
        name: ContextDefinition(**defn)
        for name, defn in DEFAULT_CONTEXTS.items()
    }


@pytest.fixture
def context_selector(default_contexts: dict[str, ContextDefinition]) -> ContextSelector:
    return ContextSelector(default_contexts)


@pytest.fixture
def classifier() -> AutoLayerClassifier:
    return AutoLayerClassifier()
