"""Research draft compatibility and immutable compilation for v0.21."""

from style_rotation.workspace.compiler import compile_research_spec
from style_rotation.workspace.contracts import (
    CompilationIssue,
    CompiledResearchSpec,
    ModelInputSlot,
    ModelPresetDescriptor,
    ResearchDraftSelection,
    SignalDescriptor,
    StrategyPresetDescriptor,
)

__all__ = [
    "CompilationIssue",
    "CompiledResearchSpec",
    "ModelInputSlot",
    "ModelPresetDescriptor",
    "ResearchDraftSelection",
    "SignalDescriptor",
    "StrategyPresetDescriptor",
    "compile_research_spec",
]
