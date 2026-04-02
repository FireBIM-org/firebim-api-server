from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/graph", tags=["graph"])


# ------------------------------------------------------------------
# Legacy / experimental graph API
# ------------------------------------------------------------------
# This API is intentionally kept minimal for now.
# It is not the new building-graph API used for RuleBook execution.
# That logic should live in:
# - app/api/graphs.py
# - app/api/checks.py
#
# The purpose of this file is simply to preserve the old endpoints so
# the app can start cleanly and existing imports/routes do not break.
# ------------------------------------------------------------------


@router.get("/count_fro/{country_code}")
def count_fro(country_code: str) -> dict:
    """
    Legacy placeholder endpoint.
    Originally used for experimental ontology/high-level graph inspection.
    """
    return {
        "success": True,
        "api": "legacy-graph",
        "endpoint": "count_fro",
        "country_code": country_code,
        "count": 0,
        "message": (
            "Legacy graph API placeholder. This endpoint is not part of the new RuleBook building-graph runtime."
        ),
    }


@router.get("/get_english/{country_code}")
def get_english(country_code: str) -> dict:
    """
    Legacy placeholder endpoint.
    """
    return {
        "success": True,
        "api": "legacy-graph",
        "endpoint": "get_english",
        "country_code": country_code,
        "text": "",
        "message": (
            "Legacy graph API placeholder. This endpoint is not part of the new RuleBook building-graph runtime."
        ),
    }


@router.get("/select_paragraph/{country_code}/{paragraph_id}")
def select_paragraph(country_code: str, paragraph_id: str) -> dict:
    """
    Legacy placeholder endpoint.
    """
    return {
        "success": True,
        "api": "legacy-graph",
        "endpoint": "select_paragraph",
        "country_code": country_code,
        "paragraph_id": paragraph_id,
        "paragraph": None,
        "message": (
            "Legacy graph API placeholder. This endpoint is not part of the new RuleBook building-graph runtime."
        ),
    }
