from __future__ import annotations

from typing import Iterable

from app.checks.compiler import RuleCompiler
from app.checks.evaluator_base import AtomicEvaluator
from app.checks.evaluator_pyshacl import PyShaclAtomicEvaluator
from app.checks.result_models import (
    CheckRunResult,
    CompiledRuleSet,
    GraphRef,
    RuleRunResult,
    RunOptions,
)
from app.checks.runtime import CheckRuntime
from app.checks.targeting import TargetResolver


class CheckEngine:
    """
    Public facade for compiled rule execution.

    Responsibilities:
    - compile rule TTL into CompiledRuleSet
    - construct GraphRef for the building graph
    - delegate actual traversal to CheckRuntime
    - support one-rule and multi-rule execution

    This first version is intentionally storage-neutral:
    - caller provides graph_ttl and rule_ttl directly
    - later we can add repository-backed adapters on top
    """

    def __init__(
        self,
        *,
        compiler: RuleCompiler | None = None,
        evaluator: AtomicEvaluator | None = None,
        target_resolver: TargetResolver | None = None,
        runtime: CheckRuntime | None = None,
    ) -> None:
        self.compiler = compiler or RuleCompiler()
        self.evaluator = evaluator or PyShaclAtomicEvaluator()
        self.target_resolver = target_resolver or TargetResolver()
        self.runtime = runtime or CheckRuntime(
            evaluator=self.evaluator,
            target_resolver=self.target_resolver,
        )

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
        """
        Execute one rule against one graph.

        Caller must provide either:
        - compiled_rule
        or
        - rule_ttl

        graph_ttl is required in this v1 engine.
        """
        if compiled_rule is None and not rule_ttl:
            raise ValueError("Either compiled_rule or rule_ttl must be provided")

        graph_ref = GraphRef(
            graph_id=graph_id,
            graph_uri=graph_uri or self._default_graph_uri(graph_id),
            ttl=graph_ttl,
        )

        compiled = compiled_rule or self.compiler.compile_from_ttl(rule_ttl or "")
        result = self.runtime.run_rule(
            graph_ref=graph_ref,
            compiled=compiled,
            options=options,
        )

        # Keep explicit caller-facing rule_id when useful for compatibility,
        # but preserve compiled rule id if there is a mismatch.
        if result.rule_id != rule_id:
            result.debug = {
                **result.debug,
                "requested_rule_id": rule_id,
                "compiled_rule_id": result.rule_id,
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
        """
        Execute multiple rules against one graph.

        For each rule_id, caller must provide either:
        - compiled_rules[rule_id]
        or
        - rule_ttls[rule_id]
        """
        rule_ttls = rule_ttls or {}
        compiled_rules = compiled_rules or {}

        results: list[RuleRunResult] = []

        for rule_id in rule_ids:
            compiled = compiled_rules.get(rule_id)
            rule_ttl = rule_ttls.get(rule_id)

            if compiled is None and not rule_ttl:
                raise ValueError(
                    f"Missing rule input for '{rule_id}': expected compiled rule or TTL"
                )

            result = self.run_rule(
                graph_id=graph_id,
                rule_id=rule_id,
                options=options,
                graph_ttl=graph_ttl,
                graph_uri=graph_uri,
                rule_ttl=rule_ttl,
                compiled_rule=compiled,
            )
            results.append(result)

        return self._aggregate_check_run_result(
            graph_id=graph_id,
            rule_results=results,
        )

    def compile_rule(self, rule_ttl: str) -> CompiledRuleSet:
        return self.compiler.compile_from_ttl(rule_ttl)

    def compile_rules(self, rule_ttls: dict[str, str]) -> dict[str, CompiledRuleSet]:
        return {
            rule_id: self.compiler.compile_from_ttl(rule_ttl)
            for rule_id, rule_ttl in rule_ttls.items()
        }

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
