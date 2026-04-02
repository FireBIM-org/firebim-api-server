from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, HTTPException
from rdflib import Graph
from rdflib.namespace import RDF, SH

try:
    from app.checks.blind_runner import BlindShaclRuleRunner
except Exception:  # pragma: no cover
    BlindShaclRuleRunner = None

from app.checks.engine import CheckEngine
from app.checks.result_models import RuleRunResult, RunOptions
from app.graphs_and_checks.publisher import (
    default_building_graph_uri,
    fetch_building_graph_ttl,
    fetch_rule_ttl,
)
from app.schemas.checks import (
    BlindCheckRunRequest,
    BlindCheckRunResponse,
    BlindCheckTerminalOutput,
    CheckCompatibilityRequest,
    CheckCompatibilityResponse,
    CheckRunEngineInfo,
    CheckRunRequest,
    CheckRunResponse,
    CheckRunSummary,
    CheckTargetResult,
    CheckTargetTraceStep,
    CompatibilityMatch,
    CompatibilityWarning,
)

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

    for _, _, construct in shacl_graph.triples((None, SH.construct, None)):
        text = str(construct)
        for part in text.split("<")[1:]:
            iri = part.split(">")[0].strip()
            if iri.startswith("http://") or iri.startswith("https://"):
                required_properties.add(iri)

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
        engine=engine_info.get("mode", "compiled_runtime"),
        inference=options.inference,
        advanced=options.advanced,
        runtime=engine_info.get("runtime"),
        evaluator=engine_info.get("evaluator"),
        target_resolver=engine_info.get("target_resolver"),
        mode=engine_info.get("mode"),
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


def _build_compatibility_response(
    *,
    rule_id: str,
    graph_id: str,
    rule_ttl: str,
    graph_ttl: str,
) -> CheckCompatibilityResponse:
    shacl_graph = _parse_ttl_to_graph(rule_ttl)
    data_graph = _parse_ttl_to_graph(graph_ttl)

    required_classes, required_properties = _required_classes_and_properties_from_rule_graph(shacl_graph)
    class_counter, property_counter = _graph_profile_from_graph(data_graph)

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

    missing_target_classes = [
        class_uri for class_uri in required_classes if class_counter.get(class_uri, 0) == 0
    ]
    missing_properties = [
        prop_uri for prop_uri in required_properties if property_counter.get(prop_uri, 0) == 0
    ]

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
    return {
        "status": "ok",
        "graph_uri_example": default_building_graph_uri("example-building"),
        "modes": [
            "compiled_runtime",
            "blind_shacl_construct",
        ],
    }


@router.post("/run", response_model=CheckRunResponse)
def run_check(payload: CheckRunRequest):
    options = RunOptions(
        inference=payload.options.inference,
        advanced=payload.options.advanced,
        debug=payload.options.debug,
        include_targets=payload.options.include_targets,
        include_trace=payload.options.include_trace,
        include_report_text=payload.options.include_report_text,
        include_report_graph_ttl=payload.options.include_report_graph_ttl,
        fail_fast=payload.options.fail_fast,
        max_steps_per_target=payload.options.max_steps_per_target,
    )

    try:
        rule_graph_uri, rule_ttl = fetch_rule_ttl(payload.rule_id)
        building_graph_uri = default_building_graph_uri(payload.graph_id)
        graph_ttl = fetch_building_graph_ttl(building_graph_uri)

        engine = CheckEngine()
        result = engine.run_rule(
            graph_id=payload.graph_id,
            rule_id=payload.rule_id,
            options=options,
            graph_ttl=graph_ttl,
            graph_uri=building_graph_uri,
            rule_ttl=rule_ttl,
        )

        return CheckRunResponse(
            rule_id=payload.rule_id,
            graph_id=payload.graph_id,
            summary=_to_check_run_summary(result),
            engine=_to_check_run_engine_info(result, options),
            targets=_to_check_target_results(result) if payload.options.include_targets else [],
            report_text=result.report_text if payload.options.include_report_text else None,
            report_graph_ttl=result.report_graph_ttl if payload.options.include_report_graph_ttl else None,
            compiled_rule=result.compiled_rule,
            debug={
                **result.debug,
                "rule_graph_uri": rule_graph_uri,
                "building_graph_uri": building_graph_uri,
                "compatibility_notes": [
                    "This endpoint now uses compiled runtime traversal",
                    "Explicit Unknown outcomes are supported",
                    "Per-target decision traces are returned when include_trace=true",
                ],
            },
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        error_bits = _error_payload(
            "check_run_error",
            "Unexpected error while executing compiled rule against building graph",
            exc,
        )
        return CheckRunResponse(
            rule_id=payload.rule_id,
            graph_id=payload.graph_id,
            summary=CheckRunSummary(),
            engine=CheckRunEngineInfo(
                engine="compiled_runtime",
                inference=payload.options.inference,
                advanced=payload.options.advanced,
                runtime="CheckRuntime",
                evaluator="PyShaclAtomicEvaluator",
                target_resolver="TargetResolver",
                mode="compiled_runtime",
            ),
            targets=[],
            report_text=None,
            report_graph_ttl=None,
            compiled_rule={},
            debug={},
            **error_bits,
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

        runner = BlindShaclRuleRunner()
        result = runner.run_rule(
            graph_id=payload.graph_id,
            graph_uri=building_graph_uri,
            graph_ttl=graph_ttl,
            rule_id=payload.rule_id,
            rule_ttl=rule_ttl,
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
            end_outputs=[
                BlindCheckTerminalOutput(**item)
                for item in result.get("end_outputs", [])
            ],
            terminal_counts=result.get("terminal_counts", {}),
            message_counts=result.get("message_counts", {}),
            debug={
                **result.debug,
                "rule_graph_uri": rule_graph_uri,
                "building_graph_uri": building_graph_uri,
                "rule_ttl_length": len(rule_ttl or ""),
                "graph_ttl_length": len(graph_ttl or ""),
                "compatibility_notes": [
                    "This endpoint now uses compiled runtime traversal",
                    "Explicit Unknown outcomes are supported",
                    "Per-target decision traces are returned when include_trace=true",
                ],
            },
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
        raise HTTPException(status_code=400, detail=str(exc))
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