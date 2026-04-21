from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.checks.caching import CheckExecutionCache, get_default_execution_cache
from app.checks.compiler import RuleCompiler
from app.checks.engine import CheckEngine
from app.checks.evaluator_base import AtomicEvaluator
from app.checks.evaluator_hybrid import HybridAtomicEvaluator
from app.checks.runtime import CheckRuntime
from app.checks.runtime_horizontal_layered import HorizontalLayeredCheckRuntime
from app.checks.runtime_vertical_pathway import VerticalPathwayCheckRuntime
from app.checks.targeting import TargetResolver

CheckSolverMode = Literal[
    "standard",
    "horizontal",
    "vertical",
]

_DEFAULT_SOLVER: CheckSolverMode = "standard"


@dataclass(frozen=True)
class CheckSolverDefinition:
    mode: CheckSolverMode
    description: str
    runtime_class_name: str
    evaluator_class_name: str
    implemented: bool = True


class CheckSolverSelector:
    _DEFINITIONS: dict[CheckSolverMode, CheckSolverDefinition] = {
        "standard": CheckSolverDefinition(
            mode="standard",
            description="Standard compiled runtime traversal with hybrid atomic evaluator.",
            runtime_class_name="CheckRuntime",
            evaluator_class_name="HybridAtomicEvaluator",
            implemented=True,
        ),
        "horizontal": CheckSolverDefinition(
            mode="horizontal",
            description="Horizontal layered batch solver that evaluates one decision row of targets at a time.",
            runtime_class_name="HorizontalLayeredCheckRuntime",
            evaluator_class_name="HybridAtomicEvaluator",
            implemented=True,
        ),
        "vertical": CheckSolverDefinition(
            mode="vertical",
            description="Vertical pathway solver that walks one linear pathway segment of rule elements at a time.",
            runtime_class_name="VerticalPathwayCheckRuntime",
            evaluator_class_name="HybridAtomicEvaluator",
            implemented=True,
        ),
    }

    def __init__(
        self,
        *,
        default_mode: str | None = None,
        cache: CheckExecutionCache | None = None,
    ) -> None:
        self.cache = cache or get_default_execution_cache()
        self.default_mode = self.normalize_mode(default_mode)

    @classmethod
    def normalize_mode(cls, mode: str | None) -> CheckSolverMode:
        if not mode:
            return _DEFAULT_SOLVER
        normalized = mode.strip().lower()
        aliases: dict[str, CheckSolverMode] = {
            "standard": "standard",
            "legacy": "standard",
            "compiled": "standard",
            "compiled_runtime": "standard",
            "legacy_compiled": "standard",
            "standard_solver": "standard",
            "horizontal": "horizontal",
            "horizontal_batch": "horizontal",
            "horizontal-layered": "horizontal",
            "horizontal_layered": "horizontal",
            "horizontal_rows": "horizontal",
            "horisontal": "horizontal",
            "horisontal_batch": "horizontal",
            "batch": "horizontal",
            "batched": "horizontal",
            "batched_compiled": "horizontal",
            "vertical": "vertical",
            "vertical_pathway": "vertical",
            "vertical-pathway": "vertical",
            "vertical_column": "vertical",
            "vertical-column": "vertical",
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            supported = ", ".join(sorted(cls._DEFINITIONS.keys()))
            raise ValueError(f"Unsupported checks solver mode '{mode}'. Supported modes: {supported}") from exc

    def resolve_mode(self, requested_mode: str | None = None) -> CheckSolverMode:
        if requested_mode:
            return self.normalize_mode(requested_mode)
        return self.default_mode

    def describe(self, requested_mode: str | None = None) -> CheckSolverDefinition:
        return self._DEFINITIONS[self.resolve_mode(requested_mode)]

    def build_engine(
        self,
        *,
        requested_mode: str | None = None,
        compiler: RuleCompiler | None = None,
        evaluator: AtomicEvaluator | None = None,
        target_resolver: TargetResolver | None = None,
    ) -> CheckEngine:
        mode = self.resolve_mode(requested_mode)
        compiler = compiler or RuleCompiler(cache=self.cache)
        evaluator = evaluator or HybridAtomicEvaluator()
        target_resolver = target_resolver or TargetResolver(cache=self.cache)

        if mode == "standard":
            runtime = CheckRuntime(
                evaluator=evaluator,
                target_resolver=target_resolver,
            )
        elif mode == "horizontal":
            runtime = HorizontalLayeredCheckRuntime(
                evaluator=evaluator,
                target_resolver=target_resolver,
            )
        elif mode == "vertical":
            runtime = VerticalPathwayCheckRuntime(
                evaluator=evaluator,
                target_resolver=target_resolver,
            )
        else:
            raise NotImplementedError(
                f"Checks solver '{mode}' is registered but not implemented yet. "
                f"Use '{_DEFAULT_SOLVER}' to keep current behavior."
            )

        return CheckEngine(
            cache=self.cache,
            compiler=compiler,
            evaluator=evaluator,
            target_resolver=target_resolver,
            runtime=runtime,
            solver_mode=mode,
        )

    def available_modes(self) -> list[dict[str, str | bool]]:
        return [
            {
                "mode": definition.mode,
                "description": definition.description,
                "implemented": definition.implemented,
            }
            for definition in self._DEFINITIONS.values()
        ]
