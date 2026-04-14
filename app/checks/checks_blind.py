from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.checks.blind_runner import BlindShaclRuleRunner
from app.graphs_and_checks.publisher import (
    default_building_graph_uri,
    fetch_building_graph_ttl,
    fetch_rule_ttl,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/checks-blind", tags=["checks-blind"])


class BlindCheckRunOptions(BaseModel):
    include_targets: bool = True
    include_debug: bool = True
    max_steps: int = 100000


class BlindCheckRunRequest(BaseModel):
    rule_id: str
    graph_id: str
    options: BlindCheckRunOptions = Field(default_factory=BlindCheckRunOptions)


class BlindCheckRunResponse(BaseModel):
    success: bool = True
    error: str | None = None
    message: str | None = None
    fallback: bool = False

    mode: str = "blind_shacl_construct"
    rule_id: str
    graph_id: str

    summary: dict[str, Any] = Field(default_factory=dict)
    end_outputs: list[dict[str, Any]] = Field(default_factory=list)
    terminal_counts: dict[str, int] = Field(default_factory=dict)
    message_counts: dict[str, int] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)


def _error_payload(error_code: str, message: str, exc: Exception) -> dict[str, Any]:
    logger.exception(message, exc_info=exc)
    return {
        "success": False,
        "error": error_code,
        "message": message,
        "fallback": True,
    }


@router.get("/health")
def checks_blind_health():
    return {
        "status": "ok",
        "mode": "blind_shacl_construct",
        "graph_uri_example": default_building_graph_uri("example-building"),
    }


@router.post("/run-blind", response_model=BlindCheckRunResponse)
def run_blind_check(payload: BlindCheckRunRequest):
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

        result["debug"] = {
            **result.get("debug", {}),
            "rule_graph_uri": rule_graph_uri,
            "building_graph_uri": building_graph_uri,
            "notes": [
                "Experimental fast path",
                "Runs sh:construct directly instead of pySHACL validate()",
                "Returns terminal end-output only",
                "Cross-class transitions are resolved independently per next decision node",
            ],
        }

        return BlindCheckRunResponse(**result)

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
            summary={},
            end_outputs=[],
            terminal_counts={},
            message_counts={},
            debug={},
            **error_bits,
        )
