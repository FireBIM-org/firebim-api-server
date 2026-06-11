from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.models.models import IfcEntity


class SamplingService:
    def __init__(self, db: Session):
        self.db = db

    def get_sampled_graph(self, model_id: str) -> dict[str, Any]:
        """
        Extract a sampled hierarchy (Project -> Site -> Building -> Storey -> Space)
        for visualization in the RuleBook.
        """
        stmt = select(IfcEntity).where(IfcEntity.model_id == model_id)
        spatial_entities = self.db.exec(stmt).all()
        entity_map = {e.global_id: e for e in spatial_entities}

        nodes = []
        for e in spatial_entities:
            nodes.append(
                {
                    "id": e.global_id,
                    "kind": self._map_type_to_kind(e.type),
                    "label": e.name or e.global_id or e.id,
                    "metadata": {"type": e.type, "globalId": e.global_id, "expressId": e.id, **e.attributes},
                }
            )

        edges = []
        for e in spatial_entities:
            if e.parent_id and e.parent_id in entity_map:
                edges.append(
                    {
                        "id": f"{e.parent_id}-{e.global_id}",
                        "source": e.parent_id,
                        "target": e.global_id,
                        "relation": "Hierarchy",
                    }
                )

        return {
            "nodes": nodes,
            "edges": edges,
        }

    def _map_type_to_kind(self, ifc_type: str) -> str:
        """
        Map IFC type to frontend BuildingGraphTargetClass.
        """
        kind = ifc_type[3:] if ifc_type.lower().startswith("ifc") else ifc_type
        return kind.capitalize()
