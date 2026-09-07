from __future__ import annotations

import logging
import traceback
from collections import Counter
from contextlib import suppress
from time import perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException
from rdflib import Graph
from rdflib.namespace import RDF, SH

try:
    from app.checks.blind_runner import BlindShaclRuleRunner
except Exception:  # pragma: no cover
    BlindShaclRuleRunner = None

from app.checks.assertions import apply_assertions, open_questions
from app.checks.compiler import RuleCompiler
from app.checks.derived import default_derived_graph_uri
from app.checks.result_models import RuleRunResult, RunOptions
from app.checks.schema_service import extract_graph_schema_payload
from app.checks.solver_selector import CheckSolverSelector
from app.core.config import settings
from app.graphs_and_checks.publisher import (
    default_building_graph_uri,
    fetch_building_graph_ttl,
    fetch_rule_ttl,
    publish_building_graph_ttl,
)
from app.schemas.checks import (
    BlindCheckRunRequest,
    BlindCheckRunResponse,
    BlindCheckTerminalOutput,
    CheckCompatibilityRequest,
    CheckCompatibilityResponse,
    CheckDerivedValue,
    CheckDiagnosticResponse,
    CheckDiagnosticTimingGroups,
    CheckEndNodeSummary,
    CheckEndNodeTargets,
    CheckQuestion,
    CheckRunEngineInfo,
    CheckRunRequest,
    CheckRunSummary,
    CheckSimpleRunResponse,
    CheckTargetResult,
    CheckTargetTraceStep,
    CompatibilityMatch,
    CompatibilityWarning,
)
from app.storage.graph_store import GraphStoreNotFoundError
from app.storage.ontology_repository import OntologyRepository


class CheckInputNotFoundError(Exception):
    """The rule or the building graph a check refers to is not in the store (e.g. a dangling `next_rule` link)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/checks", tags=["checks"])


def _error_payload(error_code: str, message: str, exc: Exception) -> dict:
    logger.exception(message, exc_info=exc)
    return {
        "success": False,
        "error": error_code,
        "message": message,
        "fallback": True,
    }


def _parse_ttl_to_graph(ttl: str) -> Graph:
    g = Graph()
    g.parse(data=ttl, format="turtle")
    return g


def _required_classes_and_properties_from_rule_graph(shacl_graph: Graph) -> tuple[list[str], list[str]]:
    required_classes = set()
    required_properties = set()

    for _, _, o in shacl_graph.triples((None, SH.targetClass, None)):
        required_classes.add(str(o))

    for _, _, o in shacl_graph.triples((None, SH.path, None)):
        required_properties.add(str(o))

    return sorted(required_classes), sorted(required_properties)


def _graph_profile_from_graph(data_graph: Graph) -> tuple[Counter, Counter]:
    class_counter: Counter[str] = Counter()
    property_counter: Counter[str] = Counter()

    for _, p, o in data_graph:
        property_counter[str(p)] += 1
        if p == RDF.type:
            class_counter[str(o)] += 1

    return class_counter, property_counter


def _to_check_run_summary(result: RuleRunResult) -> CheckRunSummary:
    return CheckRunSummary(
        total_targets=result.summary.total_targets,
        passed=result.summary.passed,
        failed=result.summary.failed,
        unknown=result.summary.unknown,
        conforms=result.summary.conforms,
        terminal_counts=result.summary.terminal_counts,
        missing_data_categories=result.summary.missing_data_categories,
    )


def _to_check_run_engine_info(result: RuleRunResult, options: RunOptions) -> CheckRunEngineInfo:
    engine_info = result.engine or {}
    return CheckRunEngineInfo(
        engine=engine_info.get("mode", "standard_compiled_runtime"),
        inference=options.inference,
        advanced=options.advanced,
        runtime=engine_info.get("runtime"),
        evaluator=engine_info.get("evaluator"),
        target_resolver=engine_info.get("target_resolver"),
        mode=engine_info.get("mode"),
        solver_mode=engine_info.get("solver_mode"),
        planner_strategy=engine_info.get("planner_strategy"),
        diagnostics_enabled=bool(engine_info.get("diagnostics_enabled")) or options.diagnostics,
        merged_schemas=list(engine_info.get("merged_schemas") or []),
    )


def _to_check_target_results(result: RuleRunResult) -> list[CheckTargetResult]:
    items: list[CheckTargetResult] = []

    for target in result.targets:
        trace = [
            CheckTargetTraceStep(
                node_id=step.node_id,
                node_type=step.node_type,
                status=step.status,
                next_node_id=step.next_node_id,
                evidence=step.evidence,
            )
            for step in target.trace
        ]

        items.append(
            CheckTargetResult(
                target_node=target.target_node,
                target_class=target.target_class,
                status=target.final_status,
                message=target.message,
                terminal_node_id=target.terminal_node_id,
                trace=trace,
                evidence=target.evidence,
            )
        )

    return items


def _solver_engine_label(solver_mode: str | None) -> str:
    labels = {
        "standard": "standard_compiled_runtime",
        "horizontal": "horizontal_compiled_runtime",
        "vertical": "vertical_compiled_runtime",
    }
    return labels.get(str(solver_mode or "").strip().lower(), "standard_compiled_runtime")


def _extract_end_node_summaries(result: RuleRunResult) -> list[CheckEndNodeSummary]:
    compiled_end_nodes = {
        item["node_id"]: item
        for item in result.compiled_rule.get("end_nodes", [])
        if isinstance(item, dict) and item.get("node_id")
    }
    terminal_counts = result.summary.terminal_counts or {}
    summaries: list[CheckEndNodeSummary] = []
    for end_node_id, count in sorted(terminal_counts.items()):
        meta = compiled_end_nodes.get(end_node_id, {})
        summaries.append(
            CheckEndNodeSummary(
                end_node_id=end_node_id,
                terminal_value=meta.get("terminal_value"),
                terminal_kind=meta.get("end_kind"),
                label=meta.get("label"),
                output_text=meta.get("output_text"),
                analysis_code=meta.get("analysis_code"),
                next_rule=meta.get("next_rule"),
                count=count,
            )
        )
    return summaries


def _extract_end_node_targets(result: RuleRunResult) -> list[CheckEndNodeTargets]:
    grouped: dict[str, list[str]] = {}
    for target in result.targets:
        if not target.terminal_node_id:
            continue
        grouped.setdefault(target.terminal_node_id, []).append(target.target_node)
    return [
        CheckEndNodeTargets(end_node_id=end_node_id, target_nodes=sorted(target_nodes))
        for end_node_id, target_nodes in sorted(grouped.items())
    ]


def _filter_completed_chains(
    completed_chains: list[dict[str, Any]],
    *,
    options: RunOptions,
    end_nodes: list[CheckEndNodeSummary],
) -> list[dict[str, Any]]:
    allowed_terminal_values = {item.lower() for item in options.diagnostic_terminal_values}
    allowed_end_node_ids = set(options.diagnostic_end_node_ids)
    analysis_codes = {item.end_node_id: item.analysis_code for item in end_nodes if item.analysis_code}
    allowed_analysis_codes = set(options.diagnostic_analysis_codes)
    filtered: list[dict[str, Any]] = []
    for chain in completed_chains:
        if options.diagnostic_success_only and chain.get("final_status") != "True":
            continue
        end_node_id = chain.get("terminal_node_id")
        if allowed_end_node_ids and end_node_id not in allowed_end_node_ids:
            continue
        if allowed_terminal_values:
            terminal_value = (chain.get("terminal_value") or "").lower()
            if terminal_value not in allowed_terminal_values:
                continue
        if allowed_analysis_codes and analysis_codes.get(end_node_id) not in allowed_analysis_codes:
            continue
        filtered.append(chain)
    return filtered


def _build_timings_payload(
    *,
    request_timings: dict[str, float],
    result_debug: dict[str, Any],
    completed_chains: list[dict[str, Any]],
) -> CheckDiagnosticTimingGroups:
    timings_ms = result_debug.get("timings_ms", {}) if isinstance(result_debug, dict) else {}
    return CheckDiagnosticTimingGroups(
        request_ms=dict(request_timings),
        rule_ms=dict(timings_ms),
        per_node_ms=dict(result_debug.get("node_timings_ms", {})),
        per_class_ms=dict(result_debug.get("class_timings_ms", {})),
        per_transition_ms=dict(result_debug.get("transition_timings_ms", {})),
        per_completed_chain_ms=[
            {
                "target_node": item.get("target_node"),
                "terminal_node_id": item.get("terminal_node_id"),
                "duration_ms": item.get("duration_ms", 0.0),
            }
            for item in completed_chains
        ],
    )


def _build_compatibility_response(
    *,
    rule_id: str,
    graph_id: str,
    rule_ttl: str,
    graph_ttl: str | None = None,
    class_counter: Counter[str] | None = None,
    property_counter: Counter[str] | None = None,
) -> CheckCompatibilityResponse:
    shacl_graph = _parse_ttl_to_graph(rule_ttl)

    if class_counter is None or property_counter is None:
        if graph_ttl is None:
            raise ValueError("Either graph_ttl or counters must be provided")

        schema_payload = extract_graph_schema_payload(
            graph_id=graph_id,
            graph_uri=default_building_graph_uri(graph_id),
            graph_ttl=graph_ttl,
        )
        payload_data = schema_payload["payload"]
        class_counter = Counter(payload_data["class_counts"])
        property_counter = Counter(payload_data["property_counts"])

    required_classes, required_properties = _required_classes_and_properties_from_rule_graph(shacl_graph)

    target_classes_found = [
        CompatibilityMatch(value=class_uri, count=class_counter.get(class_uri, 0))
        for class_uri in required_classes
        if class_counter.get(class_uri, 0) > 0
    ]
    properties_found = [
        CompatibilityMatch(value=prop_uri, count=property_counter.get(prop_uri, 0))
        for prop_uri in required_properties
        if property_counter.get(prop_uri, 0) > 0
    ]

    missing_target_classes = [class_uri for class_uri in required_classes if class_counter.get(class_uri, 0) == 0]
    missing_properties = [prop_uri for prop_uri in required_properties if property_counter.get(prop_uri, 0) == 0]

    graph_classes_unused_by_rule = [
        CompatibilityMatch(value=value, count=count)
        for value, count in sorted(class_counter.items())
        if value not in required_classes
    ]
    graph_properties_unused_by_rule = [
        CompatibilityMatch(value=value, count=count)
        for value, count in sorted(property_counter.items())
        if value not in required_properties
    ]

    total_required = len(required_classes) + len(required_properties)
    total_missing = len(missing_target_classes) + len(missing_properties)
    compatibility_score = 1.0 if total_required == 0 else max((total_required - total_missing) / total_required, 0.0)

    warnings: list[CompatibilityWarning] = []
    if missing_target_classes:
        warnings.append(
            CompatibilityWarning(
                code="missing_target_classes",
                message="One or more rule target classes do not occur in the graph.",
            )
        )
    if missing_properties:
        warnings.append(
            CompatibilityWarning(
                code="missing_properties",
                message="One or more rule properties do not occur in the graph.",
            )
        )

    return CheckCompatibilityResponse(
        rule_id=rule_id,
        graph_id=graph_id,
        compatibility_score=compatibility_score,
        target_classes_required=required_classes,
        properties_required=required_properties,
        target_classes_found=target_classes_found,
        properties_found=properties_found,
        missing_target_classes=missing_target_classes,
        missing_properties=missing_properties,
        graph_classes_unused_by_rule=graph_classes_unused_by_rule,
        graph_properties_unused_by_rule=graph_properties_unused_by_rule,
        warnings=warnings,
    )


@router.get("/health")
def checks_health():
    selector = CheckSolverSelector(default_mode=settings.CHECKS_SOLVER)
    return {
        "status": "ok",
        "graph_uri_example": default_building_graph_uri("example-building"),
        "modes": [
            "standard",
            "horizontal",
            "vertical",
            "blind_shacl_construct",
        ],
        "default_solver": selector.resolve_mode(),
        "solvers": selector.available_modes(),
    }


def _build_run_options(payload: CheckRunRequest, *, diagnostics: bool) -> RunOptions:
    return RunOptions(
        inference=payload.options.inference,
        advanced=payload.options.advanced,
        debug=payload.options.debug,
        diagnostics=diagnostics,
        include_targets=payload.options.include_targets or payload.options.include_end_node_targets or diagnostics,
        include_trace=payload.options.include_trace or diagnostics,
        include_report_text=payload.options.include_report_text,
        include_report_graph_ttl=payload.options.include_report_graph_ttl if diagnostics else False,
        include_end_node_targets=payload.options.include_end_node_targets,
        fail_fast=payload.options.fail_fast,
        max_steps_per_target=payload.options.max_steps_per_target,
        planner_strategy=payload.options.planner_strategy,
        include_planner_debug=payload.options.include_planner_debug or diagnostics,
        diagnostic_terminal_values=payload.options.diagnostic_terminal_values,
        diagnostic_end_node_ids=payload.options.diagnostic_end_node_ids,
        diagnostic_analysis_codes=payload.options.diagnostic_analysis_codes,
        diagnostic_success_only=payload.options.diagnostic_success_only,
        persist_derived=payload.options.persist_derived,
        use_derived_from=list(payload.options.use_derived_from),
        target_nodes=list(payload.options.target_nodes),
    )


def _execute_check_run(
    payload: CheckRunRequest,
    *,
    forced_solver: str | None = None,
    diagnostics: bool,
) -> tuple[RunOptions, str | None, str, dict[str, float], str, str, CheckCompatibilityResponse, RuleRunResult]:
    options = _build_run_options(payload, diagnostics=diagnostics)

    requested_solver = forced_solver or payload.options.solver
    solver_selector = CheckSolverSelector(default_mode=settings.CHECKS_SOLVER)
    selected_solver = solver_selector.resolve_mode(requested_solver)
    request_timings: dict[str, float] = {}

    started = perf_counter()
    try:
        rule_graph_uri, rule_ttl = fetch_rule_ttl(payload.rule_id)
    except (GraphStoreNotFoundError, ValueError) as exc:
        raise CheckInputNotFoundError(
            "rule_not_found", f"Rule '{payload.rule_id}' is not published (is it linked from another rule?)"
        ) from exc
    request_timings["fetch_rule"] = round((perf_counter() - started) * 1000.0, 3)

    building_graph_uri = default_building_graph_uri(payload.graph_id)
    started = perf_counter()
    try:
        graph_ttl = fetch_building_graph_ttl(building_graph_uri)
    except GraphStoreNotFoundError as exc:
        raise CheckInputNotFoundError(
            "graph_not_found", f"Building graph '{payload.graph_id}' is not published"
        ) from exc
    if payload.options.use_derived_from:
        graph_ttl = merge_derived_graphs(
            graph_ttl=graph_ttl, graph_id=payload.graph_id, rule_ids=payload.options.use_derived_from
        )
    if payload.options.overrides:
        graph_ttl, _ = apply_assertions(graph_ttl, payload.options.overrides)
    graph_ttl, merged_schemas = merge_schema_graphs(
        graph_ttl=graph_ttl, rule_ttl=rule_ttl, schema=payload.options.schema
    )
    request_timings["fetch_graph"] = round((perf_counter() - started) * 1000.0, 3)

    started = perf_counter()
    compatibility = _build_compatibility_response(
        rule_id=payload.rule_id,
        graph_id=payload.graph_id,
        rule_ttl=rule_ttl,
        graph_ttl=graph_ttl,
    )
    request_timings["compatibility"] = round((perf_counter() - started) * 1000.0, 3)

    started = perf_counter()
    engine = solver_selector.build_engine(requested_mode=requested_solver)
    request_timings["build_engine"] = round((perf_counter() - started) * 1000.0, 3)

    started = perf_counter()
    result = engine.run_rule(
        graph_id=payload.graph_id,
        rule_id=payload.rule_id,
        options=options,
        graph_ttl=graph_ttl,
        graph_uri=building_graph_uri,
        rule_ttl=rule_ttl,
    )
    request_timings["run_rule"] = round((perf_counter() - started) * 1000.0, 3)
    if merged_schemas and isinstance(getattr(result, "engine", None), dict):
        result.engine["merged_schemas"] = merged_schemas
    if payload.options.include_questions and hasattr(result, "targets"):
        started = perf_counter()
        try:
            compiled = RuleCompiler().compile_from_ttl(rule_ttl)
            result.questions = open_questions(result=result, graph_ttl=graph_ttl, compiled=compiled)
        except Exception as exc:  # questions are a convenience, never a reason to fail the run
            logger.info("Could not derive open questions: %s", exc)
        request_timings["questions"] = round((perf_counter() - started) * 1000.0, 3)
    if (
        options.persist_derived
        and getattr(result, "derived_graph_ttl", None)
        and getattr(result, "derived_graph_uri", None)
    ):
        started = perf_counter()
        publish_building_graph_ttl(ttl_text=result.derived_graph_ttl, graph_uri=result.derived_graph_uri)
        result.engine["derived_persisted"] = True
        request_timings["persist_derived"] = round((perf_counter() - started) * 1000.0, 3)
    return (
        options,
        requested_solver,
        selected_solver,
        request_timings,
        rule_graph_uri,
        building_graph_uri,
        compatibility,
        result,
    )


def _to_derived_values(result: RuleRunResult) -> list[CheckDerivedValue]:
    values: list[CheckDerivedValue] = []
    for record in getattr(result, "derived_values", None) or []:
        values.append(
            CheckDerivedValue(
                target=str(record.get("target")),
                property=str(record.get("property")),
                value=record.get("value"),
                node_id=record.get("node_id"),
                expression=record.get("expression"),
                inputs=dict(record.get("inputs") or {}),
                overwrote_previous=record.get("overwrote_previous"),
            )
        )
    return values


_ontology_repo = OntologyRepository()
_ONTOLOGY_SUMMARY_CACHE: dict[str, Any] = {"at": 0.0, "summaries": []}
_ONTOLOGY_SUMMARY_TTL_SECONDS = 60.0


def _published_ontology_summaries() -> list[dict[str, Any]]:
    """Cached list of published ontologies (a failed store call is cached too, so a check never waits twice)."""
    now = perf_counter()
    if now - float(_ONTOLOGY_SUMMARY_CACHE["at"]) < _ONTOLOGY_SUMMARY_TTL_SECONDS:
        return list(_ONTOLOGY_SUMMARY_CACHE["summaries"])
    try:
        summaries = list(_ontology_repo.list_summaries())
    except Exception:  # store unavailable: run without schema
        summaries = []
    _ONTOLOGY_SUMMARY_CACHE.update({"at": now, "summaries": summaries})
    return list(summaries)


def _ontologies_used_by_rule(rule_ttl: str) -> list[tuple[str, str, str]]:
    """(family, country, version-token) of every published ontology whose namespace occurs in the rule."""
    found: list[tuple[str, str, str]] = []
    for summary in _published_ontology_summaries():
        namespace = str(summary.get("canonical_country_uri") or "")
        if namespace and namespace in rule_ttl and summary.get("default_version"):
            found.append((str(summary["ontology_family"]), str(summary["country"]), str(summary["default_version"])))
    return found


def merge_schema_graphs(*, graph_ttl: str, rule_ttl: str, schema: str | None) -> tuple[str, list[str]]:
    """Append the schema subset (sub-class / sub-property / equivalence / inverse triples) of the ontologies the
    rule uses, so that `target_class` matches sub-classes and a rule can read `fbo:X` when the graph says
    `fbo-dk:X` (declared `rdfs:subPropertyOf fbo:X`). Returns (ttl, list of 'family/country/version' merged)."""
    from app.checks.rdfs_light import schema_subset

    mode = (schema or "auto").strip().lower()
    if mode == "none":
        return graph_ttl, []
    if mode == "auto":
        selections = _ontologies_used_by_rule(rule_ttl)
    else:
        selections = []
        for item in mode.split(","):
            parts = [p for p in item.strip().split("/") if p]
            if len(parts) == 2:
                selections.append((parts[0], parts[1], "default"))
            elif len(parts) == 3:
                selections.append((parts[0], parts[1], parts[2]))
    if not selections:
        return graph_ttl, []
    merged = Graph()
    merged.parse(data=graph_ttl, format="turtle")
    used: list[str] = []
    for family, country, version in selections:
        try:
            fetched = _ontology_repo.fetch_version(family, country, version)
        except Exception:
            logger.info("No published ontology %s/%s/%s to merge as schema", family, country, version)
            continue
        for triple in schema_subset(fetched.ttl):
            merged.add(triple)
        used.append(f"{family}/{country}/{fetched.version.version}")
    if not used:
        return graph_ttl, []
    return merged.serialize(format="turtle"), used


def merge_derived_graphs(*, graph_ttl: str, graph_id: str, rule_ids: list[str]) -> str:
    """Append the persisted derived graphs of earlier rules to the building graph TTL (missing ones are skipped)."""
    from rdflib import Graph

    merged = Graph()
    merged.parse(data=graph_ttl, format="turtle")
    for rule_id in rule_ids:
        derived_uri = default_derived_graph_uri(graph_id, rule_id)
        try:
            derived_ttl = fetch_building_graph_ttl(derived_uri)
        except Exception:
            logger.info("No persisted derived graph for rule '%s' on graph '%s'", rule_id, graph_id)
            continue
        overlay = Graph()
        overlay.parse(data=derived_ttl, format="turtle")
        # derived values shadow base values for the same (subject, predicate)
        for subj, pred in {(s, p) for s, p, _ in overlay if not str(p).startswith("http://www.w3.org/ns/prov#")}:
            merged.remove((subj, pred, None))
        for triple in overlay:
            merged.add(triple)
    return merged.serialize(format="turtle")


def _build_simple_response(
    *,
    payload: CheckRunRequest,
    options: RunOptions,
    requested_solver: str | None,
    selected_solver: str,
    request_timings: dict[str, float],
    rule_graph_uri: str,
    building_graph_uri: str,
    compatibility: CheckCompatibilityResponse,
    result: RuleRunResult,
) -> CheckSimpleRunResponse:
    debug_payload = {}
    if payload.options.debug:
        debug_payload = {
            "requested_solver": requested_solver,
            "selected_solver": selected_solver,
            "rule_graph_uri": rule_graph_uri,
            "building_graph_uri": building_graph_uri,
            "diagnostics_enabled": options.diagnostics,
        }
    return CheckSimpleRunResponse(
        success=True,
        rule_id=payload.rule_id,
        graph_id=payload.graph_id,
        solver=selected_solver,
        start_node_id=result.compiled_rule.get("start_node_id") or result.compiled_rule.get("entry_node_id"),
        summary=_to_check_run_summary(result),
        end_nodes=_extract_end_node_summaries(result),
        end_node_targets=_extract_end_node_targets(result) if payload.options.include_end_node_targets else [],
        engine=_to_check_run_engine_info(result, options),
        compatibility=compatibility.model_dump(),
        report_text=(result.report_text if payload.options.include_report_text else None),
        debug=debug_payload,
        derived_values=_to_derived_values(result),
        derived_graph_uri=getattr(result, "derived_graph_uri", None),
        derived_persisted=bool(result.engine.get("derived_persisted")),
        questions=[CheckQuestion(**q) for q in (getattr(result, "questions", None) or [])],
    )


def _build_diagnostic_response(
    *,
    payload: CheckRunRequest,
    options: RunOptions,
    requested_solver: str | None,
    selected_solver: str,
    request_timings: dict[str, float],
    rule_graph_uri: str,
    building_graph_uri: str,
    compatibility: CheckCompatibilityResponse,
    result: RuleRunResult,
) -> CheckDiagnosticResponse:
    result_debug = result.debug if isinstance(result.debug, dict) else {}
    end_nodes = _extract_end_node_summaries(result)
    completed_chains = _filter_completed_chains(
        list(result_debug.get("completed_chains", [])),
        options=options,
        end_nodes=end_nodes,
    )
    trace_payload = []
    if hasattr(result, "trace") and result.trace is not None:
        trace_payload = result.trace
    elif isinstance(result.debug, dict):
        trace_payload = result.debug.get("trace", [])
    return CheckDiagnosticResponse(
        success=True,
        rule_id=payload.rule_id,
        graph_id=payload.graph_id,
        solver=selected_solver,
        start_node_id=result.compiled_rule.get("start_node_id") or result.compiled_rule.get("entry_node_id"),
        summary=_to_check_run_summary(result),
        end_nodes=end_nodes,
        end_node_targets=_extract_end_node_targets(result) if payload.options.include_end_node_targets else [],
        engine=_to_check_run_engine_info(result, options),
        compatibility=compatibility.model_dump(),
        report_text=result.report_text if payload.options.include_report_text else None,
        debug={
            "requested_solver": requested_solver,
            "selected_solver": selected_solver,
            "rule_graph_uri": rule_graph_uri,
            "building_graph_uri": building_graph_uri,
            "counters": result_debug.get("counters", {}),
        },
        compiled_rule=result.compiled_rule if isinstance(result.compiled_rule, dict) else {},
        derived_values=_to_derived_values(result),
        derived_graph_uri=getattr(result, "derived_graph_uri", None),
        derived_persisted=bool(result.engine.get("derived_persisted")),
        questions=[CheckQuestion(**q) for q in (getattr(result, "questions", None) or [])],
        targets=_to_check_target_results(result),
        trace=trace_payload,
        timings=_build_timings_payload(
            request_timings=request_timings,
            result_debug=result_debug,
            completed_chains=completed_chains,
        ),
        planner=result_debug.get("planner", {}),
        transition_inspections=list(result_debug.get("transition_inspections", [])),
        node_inspections=list(result_debug.get("node_inspections", [])),
        terminal_failures=list(result_debug.get("terminal_failures", [])),
        sequence_events=list(result_debug.get("sequence_events", [])),
        completed_chains=completed_chains,
    )


def _error_run_response(
    *,
    payload: CheckRunRequest,
    selected_solver: str | None,
    requested_solver: str | None,
    diagnostics: bool,
    exc: Exception,
):
    if isinstance(exc, CheckInputNotFoundError):
        # A dangling rule link (end element `next_rule`), a deleted rule or an unknown graph: say which.
        error_bits = _error_payload(exc.code, str(exc), exc)
    else:
        error_bits = _error_payload(
            "check_run_error",
            "Unexpected error while executing compiled rule against building graph",
            exc,
        )
    error_debug = {
        "requested_solver": requested_solver,
        "selected_solver": selected_solver,
    }
    if payload.options.debug:
        error_debug.update(
            {
                "exception_type": exc.__class__.__name__,
                "exception_message": str(exc),
                "traceback": traceback.format_exc().splitlines(),
            }
        )
    engine = CheckRunEngineInfo(
        engine=_solver_engine_label(selected_solver),
        inference=payload.options.inference,
        advanced=payload.options.advanced,
        solver_mode=selected_solver,
        diagnostics_enabled=diagnostics,
    )
    if diagnostics:
        return CheckDiagnosticResponse(
            **error_bits,
            rule_id=payload.rule_id,
            graph_id=payload.graph_id,
            solver=selected_solver or CheckSolverSelector(default_mode=settings.CHECKS_SOLVER).resolve_mode(),
            start_node_id=None,
            summary=CheckRunSummary(),
            end_nodes=[],
            end_node_targets=[],
            engine=engine,
            compatibility={},
            report_text="" if payload.options.include_report_text else None,
            debug=error_debug if payload.options.debug else {},
            compiled_rule={},
            targets=[],
            trace=[],
            timings=CheckDiagnosticTimingGroups(),
            planner={},
            transition_inspections=[],
            node_inspections=[],
            terminal_failures=[],
            sequence_events=[],
            completed_chains=[],
        )
    return CheckSimpleRunResponse(
        **error_bits,
        rule_id=payload.rule_id,
        graph_id=payload.graph_id,
        solver=selected_solver or CheckSolverSelector(default_mode=settings.CHECKS_SOLVER).resolve_mode(),
        start_node_id=None,
        summary=CheckRunSummary(),
        end_nodes=[],
        end_node_targets=[],
        engine=engine,
        compatibility={},
        report_text="" if payload.options.include_report_text else None,
        debug=error_debug if payload.options.debug else {},
    )


@router.post("/run", response_model=CheckSimpleRunResponse)
def run_check(payload: CheckRunRequest):
    try:
        (
            options,
            requested_solver,
            selected_solver,
            request_timings,
            rule_graph_uri,
            building_graph_uri,
            compatibility,
            result,
        ) = _execute_check_run(payload, diagnostics=False)
        return _build_simple_response(
            payload=payload,
            options=options,
            requested_solver=requested_solver,
            selected_solver=selected_solver,
            request_timings=request_timings,
            rule_graph_uri=rule_graph_uri,
            building_graph_uri=building_graph_uri,
            compatibility=compatibility,
            result=result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        selected_solver = None
        with suppress(Exception):
            selected_solver = CheckSolverSelector(default_mode=settings.CHECKS_SOLVER).resolve_mode(
                payload.options.solver
            )
        return _error_run_response(
            payload=payload,
            selected_solver=selected_solver,
            requested_solver=payload.options.solver,
            diagnostics=False,
            exc=exc,
        )


@router.post("/run-vertical", response_model=CheckSimpleRunResponse)
def run_check_vertical(payload: CheckRunRequest):
    try:
        (
            options,
            requested_solver,
            selected_solver,
            request_timings,
            rule_graph_uri,
            building_graph_uri,
            compatibility,
            result,
        ) = _execute_check_run(payload, forced_solver="vertical", diagnostics=False)
        return _build_simple_response(
            payload=payload,
            options=options,
            requested_solver=requested_solver,
            selected_solver=selected_solver,
            request_timings=request_timings,
            rule_graph_uri=rule_graph_uri,
            building_graph_uri=building_graph_uri,
            compatibility=compatibility,
            result=result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        return _error_run_response(
            payload=payload,
            selected_solver="vertical",
            requested_solver="vertical",
            diagnostics=False,
            exc=exc,
        )


@router.post("/run-horisontal", response_model=CheckSimpleRunResponse)
def run_check_horisontal(payload: CheckRunRequest):
    try:
        (
            options,
            requested_solver,
            selected_solver,
            request_timings,
            rule_graph_uri,
            building_graph_uri,
            compatibility,
            result,
        ) = _execute_check_run(payload, forced_solver="horizontal", diagnostics=False)
        return _build_simple_response(
            payload=payload,
            options=options,
            requested_solver=requested_solver,
            selected_solver=selected_solver,
            request_timings=request_timings,
            rule_graph_uri=rule_graph_uri,
            building_graph_uri=building_graph_uri,
            compatibility=compatibility,
            result=result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        return _error_run_response(
            payload=payload,
            selected_solver="horizontal",
            requested_solver="horizontal",
            diagnostics=False,
            exc=exc,
        )


@router.post("/run-diagnostic", response_model=CheckDiagnosticResponse)
def run_check_diagnostic(payload: CheckRunRequest):
    try:
        (
            options,
            requested_solver,
            selected_solver,
            request_timings,
            rule_graph_uri,
            building_graph_uri,
            compatibility,
            result,
        ) = _execute_check_run(payload, diagnostics=True)
        return _build_diagnostic_response(
            payload=payload,
            options=options,
            requested_solver=requested_solver,
            selected_solver=selected_solver,
            request_timings=request_timings,
            rule_graph_uri=rule_graph_uri,
            building_graph_uri=building_graph_uri,
            compatibility=compatibility,
            result=result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        selected_solver = None
        with suppress(Exception):
            selected_solver = CheckSolverSelector(default_mode=settings.CHECKS_SOLVER).resolve_mode(
                payload.options.solver
            )
        return _error_run_response(
            payload=payload,
            selected_solver=selected_solver,
            requested_solver=payload.options.solver,
            diagnostics=True,
            exc=exc,
        )


@router.post("/run-blind", response_model=BlindCheckRunResponse)
def run_blind_check(payload: BlindCheckRunRequest):
    if BlindShaclRuleRunner is None:
        raise HTTPException(
            status_code=501,
            detail="Blind SHACL runner is not available in this deployment.",
        )
    try:
        rule_graph_uri, rule_ttl = fetch_rule_ttl(payload.rule_id)
        building_graph_uri = default_building_graph_uri(payload.graph_id)
        graph_ttl = fetch_building_graph_ttl(building_graph_uri)
        if payload.options.use_derived_from:
            graph_ttl = merge_derived_graphs(
                graph_ttl=graph_ttl, graph_id=payload.graph_id, rule_ids=payload.options.use_derived_from
            )
        if payload.options.overrides:
            graph_ttl, _ = apply_assertions(graph_ttl, payload.options.overrides)
        graph_ttl, _ = merge_schema_graphs(graph_ttl=graph_ttl, rule_ttl=rule_ttl, schema=payload.options.schema)

        runner = BlindShaclRuleRunner()
        result = runner.run_rule(
            graph_id=payload.graph_id,
            graph_uri=building_graph_uri,
            graph_ttl=graph_ttl,
            rule_id=payload.rule_id,
            rule_ttl=rule_ttl,
            target_nodes=list(payload.options.target_nodes),
            include_targets=payload.options.include_targets,
            include_debug=payload.options.include_debug,
            max_steps=payload.options.max_steps,
        )

        return BlindCheckRunResponse(
            success=result.get("success", True),
            error=result.get("error"),
            message=result.get("message"),
            fallback=result.get("fallback", False),
            mode=result.get("mode", "blind_shacl_construct"),
            rule_id=payload.rule_id,
            graph_id=payload.graph_id,
            summary=CheckRunSummary(**result.get("summary", {})),
            end_outputs=[BlindCheckTerminalOutput(**item) for item in result.get("end_outputs", [])],
            terminal_counts=result.get("terminal_counts", {}),
            message_counts=result.get("message_counts", {}),
            derived_values=[CheckDerivedValue(**item) for item in result.get("derived_values", [])],
            debug={
                **(result.get("debug") or {}),
                "rule_graph_uri": rule_graph_uri,
                "building_graph_uri": building_graph_uri,
                "rule_ttl_length": len(rule_ttl or ""),
                "graph_ttl_length": len(graph_ttl or ""),
                "compatibility_notes": [
                    "This endpoint uses blind SHACL CONSTRUCT via BlindShaclRuleRunner.",
                    "Only terminal outputs and aggregate terminal/message counts are returned.",
                    (
                        "Per-target decision traces are not available from /checks/run-blind; "
                        "use /checks/run for traced execution."
                    ),
                ],
            },
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _error_payload(
            "blind_check_run_error",
            "Unexpected error while executing blind SHACL construct run",
            exc,
        )
        return BlindCheckRunResponse(
            rule_id=payload.rule_id,
            graph_id=payload.graph_id,
            summary=CheckRunSummary(),
            end_outputs=[],
            terminal_counts={},
            message_counts={},
            debug={},
            **error_bits,
        )


@router.post("/compatibility", response_model=CheckCompatibilityResponse)
def check_compatibility(payload: CheckCompatibilityRequest):
    try:
        _, rule_ttl = fetch_rule_ttl(payload.rule_id)
        building_graph_uri = default_building_graph_uri(payload.graph_id)
        graph_ttl = fetch_building_graph_ttl(building_graph_uri)
        return _build_compatibility_response(
            rule_id=payload.rule_id,
            graph_id=payload.graph_id,
            rule_ttl=rule_ttl,
            graph_ttl=graph_ttl,
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _error_payload(
            "check_compatibility_error",
            "Unexpected error while computing compatibility",
            exc,
        )
        return CheckCompatibilityResponse(
            rule_id=payload.rule_id,
            graph_id=payload.graph_id,
            **error_bits,
        )
