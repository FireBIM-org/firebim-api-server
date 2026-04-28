from __future__ import annotations

from pathlib import Path

from app.storage.building_repository import BuildingRepository

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

GRAPH_FIXTURES: dict[str, str] = {
    "ifc-inspired-small": "building_small_ifc_inspired.ttl",
    "ifc-inspired-medium": "building_medium_ifc_inspired.ttl",
    "ifc-inspired-large": "building_large_ifc_inspired.ttl",
    "ifc-only-graph-compliant": "building_ifc_only_compliant.ttl",
    "fbo-only-graph-compliant": "building_fbo_only_compliant.ttl",
    "ifc-fbo-graph-compliant": "building_ifc_fbo_compliant.ttl",
    "ifc-rir-fbo-graph-compliant": "building_ifc_rir_fbo_compliant.ttl",
}


def _load_fixture_ttl(filename: str) -> str:
    return (FIXTURE_DIR / filename).read_text(encoding="utf-8")


def bootstrap_default_demo_content(repo: BuildingRepository | None = None) -> list[dict[str, object]]:
    repository = repo or BuildingRepository()
    seeded: list[dict[str, object]] = []

    for graph_id, filename in GRAPH_FIXTURES.items():
        graph_uri, overwritten = repository.publish_graph(
            graph_id=graph_id,
            ttl=_load_fixture_ttl(filename),
        )
        seeded.append(
            {
                "graph_id": graph_id,
                "graph_uri": graph_uri,
                "fixture": filename,
                "overwritten": overwritten,
            }
        )

    return seeded

