from __future__ import annotations

from collections.abc import Iterable
from time import perf_counter

from app.checks.caching import CheckExecutionCache, accumulate_timing, get_default_execution_cache
from app.checks.compiler import RuleCompiler
from app.checks.evaluator_base import AtomicEvaluator
from app.checks.evaluator_hybrid import HybridAtomicEvaluator
from app.checks.result_models import CheckRunResult, CompiledRuleSet, GraphRef, RuleRunResult, RunOptions
from app.checks.runtime import CheckRuntime
from app.checks.targeting import TargetResolver


class CheckEngine:
    def __init__(
        self,
        *,
        cache: CheckExecutionCache | None = None,
        compiler: RuleCompiler | None = None,
        evaluator: AtomicEvaluator | None = None,
        target_resolver: TargetResolver | None = None,
        runtime: CheckRuntime | None = None,
        solver_mode: str = "standard",
    ) -> None:
        self.cache = cache or get_default_execution_cache()
        self.compiler = compiler or RuleCompiler(cache=self.cache)

        self.evaluator = evaluator or HybridAtomicEvaluator()

        self.target_resolver = target_resolver or TargetResolver(cache=self.cache)
        self.runtime = runtime or CheckRuntime(
            evaluator=self.evaluator,
            target_resolver=self.target_resolver,
        )
        self.solver_mode = solver_mode

    def run_rule(
        self,
        graph_id: str,
        rule_id: str,
        options: RunOptions,
        *,
        graph_ttl: str,
        graph_uri: str | None = None,
        rule_ttl: str | None = None,
        compiled_rule: CompiledRuleSet | None = None,
    ) -> RuleRunResult:
        if compiled_rule is None and not rule_ttl:
            raise ValueError("Either compiled_rule or rule_ttl must be provided")

        graph_ref = GraphRef(
            graph_id=graph_id,
            graph_uri=graph_uri or self._default_graph_uri(graph_id),
            ttl=graph_ttl,
        )

        metrics: dict | None = {} if options.diagnostics else None
        if compiled_rule is None:
            started = perf_counter()
            compiled = self.compiler.compile_from_ttl(rule_ttl or "", metrics=metrics)
            accumulate_timing(metrics, "compile_rule_total", perf_counter() - started)
        else:
            compiled = compiled_rule

        result = self.runtime.run_rule(
            graph_ref=graph_ref,
            compiled=compiled,
            options=options,
            metrics=metrics,
        )

        if result.rule_id != rule_id:
            result.debug = {
                **result.debug,
                "requested_rule_id": rule_id,
                "compiled_rule_id": result.rule_id,
            }

        result.engine = {
            **(result.engine or {}),
            "solver_mode": self.solver_mode,
        }

        if metrics is not None:
            result.debug = {
                **result.debug,
                "timings_ms": metrics.get("timings_ms", {}),
                "counters": metrics.get("counters", {}),
            }
        return result

    def run_rules(
        self,
        graph_id: str,
        rule_ids: list[str],
        options: RunOptions,
        *,
        graph_ttl: str,
        graph_uri: str | None = None,
        rule_ttls: dict[str, str] | None = None,
        compiled_rules: dict[str, CompiledRuleSet] | None = None,
    ) -> CheckRunResult:
        rule_ttls = rule_ttls or {}
        compiled_rules = compiled_rules or {}
        results: list[RuleRunResult] = []

        for rule_id in rule_ids:
            compiled = compiled_rules.get(rule_id)
            rule_ttl = rule_ttls.get(rule_id)
            if compiled is None and not rule_ttl:
                raise ValueError(f"Missing rule input for '{rule_id}': expected compiled rule or TTL")
            results.append(
                self.run_rule(
                    graph_id=graph_id,
                    rule_id=rule_id,
                    options=options,
                    graph_ttl=graph_ttl,
                    graph_uri=graph_uri,
                    rule_ttl=rule_ttl,
                    compiled_rule=compiled,
                )
            )

        return self._aggregate_check_run_result(graph_id=graph_id, rule_results=results)

    def compile_rule(self, rule_ttl: str) -> CompiledRuleSet:
        return self.compiler.compile_from_ttl(rule_ttl)

    def compile_rules(self, rule_ttls: dict[str, str]) -> dict[str, CompiledRuleSet]:
        return {rule_id: self.compiler.compile_from_ttl(rule_ttl) for rule_id, rule_ttl in rule_ttls.items()}

    def _aggregate_check_run_result(
        self,
        *,
        graph_id: str,
        rule_results: Iterable[RuleRunResult],
    ) -> CheckRunResult:
        rule_results = list(rule_results)
        return CheckRunResult(
            graph_id=graph_id,
            rule_results=rule_results,
            total_rules=len(rule_results),
            total_targets=sum(item.summary.total_targets for item in rule_results),
            passed=sum(item.summary.passed for item in rule_results),
            failed=sum(item.summary.failed for item in rule_results),
            unknown=sum(item.summary.unknown for item in rule_results),
            debug={
                "rule_ids": [item.rule_id for item in rule_results],
                "engine": self.__class__.__name__,
                "runtime": self.runtime.__class__.__name__,
                "evaluator": self.evaluator.__class__.__name__,
            },
        )

    def _default_graph_uri(self, graph_id: str) -> str:
        return f"https://firebim.org/graphs/buildings/{graph_id}"
