"""One authorized selection of an immutable destination and its retained schema."""

import json
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import unquote

from fastapi import HTTPException
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDFS
from sqlmodel import Session, select

from app.checks.rdfs_light import materialize, schema_subset
from app.ingestion.display import validate_display_index
from app.ingestion.jobs import AdmissionError
from app.ingestion.policy import LIMITS
from app.models.models import IfcModel, PublishedGraph, User
from app.storage.building_repository import BUILDING_GRAPH_BASE_URI, BuildingRepository, default_building_graph_uri
from app.storage.graph_store import GraphStoreError, GraphStoreNotFoundError
from app.storage.ingestion_objects import IngestionObjects

PREVIEW_TRIPLES = 1000
PREVIEW_NODES = 500
PREVIEW_BYTES = 256 * 1024
CONTEXT_BYTES = 16 * 1024 * 1024


def graph_identifier(value: str, *, strict: bool = False) -> str:
    value = value.strip()
    if value.startswith(BUILDING_GRAPH_BASE_URI):
        value = value[len(BUILDING_GRAPH_BASE_URI) :]
    if not value or strict and any(char in value for char in "/#?"):
        raise HTTPException(400, "Expected a graph ID or canonical building graph URI")
    return value


class LegacyGraphNotFound(HTTPException):
    def __init__(self):
        super().__init__(404, "Graph not found")


@dataclass
class ResolvedGraph:
    graph_id: str
    graph_uri: str
    backend: str
    ttl: str | None = None
    publication: PublishedGraph | None = None
    ontologies: list[tuple[dict, str]] = field(default_factory=list)

    def qualified_ttl(self) -> str:
        if self.ttl is None:
            raise HTTPException(422, "This legacy SQL model has no materialized rule dataset")
        if not self.publication:
            return self.ttl
        graph = Graph().parse(data=self.ttl, format="turtle")
        for _, ttl in self.ontologies:
            for triple in schema_subset(ttl):
                graph.add(triple)
        if len(graph) > LIMITS.triples:
            raise HTTPException(413, "Qualified graph exceeds the evaluator triple budget")
        return graph.serialize(format="turtle")

    def evidence_ttl(self) -> str:
        if not self.publication:
            return self.qualified_ttl()
        destination = Graph().parse(data=self.ttl, format="turtle")
        qualified = Graph().parse(data=self.qualified_ttl(), format="turtle")
        schema_only = set(qualified) - set(destination)
        materialize(qualified)
        for triple in schema_only:
            qualified.remove(triple)
        return qualified.serialize(format="turtle")

    def preview(self, display_index=None) -> dict:
        graph = Graph().parse(data=self.ttl or "", format="turtle")
        sample = Graph()
        nodes = {}
        edges = []
        used_bytes = 0
        destination_labels, ontology_labels = {}, {}

        def retain_labels(source, target):
            for subject, label in source.subject_objects(RDFS.label):
                if isinstance(label, Literal):
                    target.setdefault(str(subject), []).append(label)

        retain_labels(graph, destination_labels)
        for _, ttl in self.ontologies:
            retain_labels(Graph().parse(data=ttl, format="turtle"), ontology_labels)

        def preferred_label(labels):
            if not labels:
                return None

            def sort_key(label):
                language = (label.language or "").lower()
                priority = 0 if language == "en" or language.startswith("en-") else 1 if not language else 2
                return priority, language, str(label)

            value = sorted((item for item in labels if str(item).strip()), key=sort_key)
            return str(value[0]).strip() if value else None

        display_entities = display_index.get("entities", {}) if isinstance(display_index, dict) else {}

        def identity(term):
            if isinstance(term, Literal):
                return '"literal:' + sha256(term.n3().encode()).hexdigest() + '"'
            return "_:" + str(term) if isinstance(term, BNode) else str(term)

        def display_label(term):
            iri = str(term)
            entry = display_entities.get(iri) if isinstance(term, URIRef) else None
            label, label_source = None, None
            if isinstance(term, URIRef):
                label = preferred_label(destination_labels.get(iri))
                if label is not None:
                    label_source = "destination:rdfs:label"
                else:
                    label = preferred_label(ontology_labels.get(iri))
                    if label is not None:
                        label_source = "ontology:rdfs:label"
            if label is None and entry and entry.get("name"):
                label = entry["name"]
                label_source = entry["label_source"]
            if label is None and entry and entry.get("ifc_class"):
                identifier = unquote(iri.split(":entity:", 1)[1]) if ":entity:" in iri else iri.rsplit(":", 1)[-1]
                if len(identifier) > 16:
                    identifier = "…" + identifier[-12:]
                label = f"{entry['ifc_class']} · {identifier}"
                label_source = "ifc_class"
            return (label or iri)[:256], entry, label_source

        total_nodes = {identity(term) for s, _, o in graph for term in (s, o)}
        for subject, predicate, obj in sorted(graph, key=lambda triple: tuple(term.n3() for term in triple)):
            size = sum(len(term.n3().encode()) for term in (subject, predicate, obj)) + 6
            source, target = identity(subject), identity(obj)
            if (
                len(sample) >= PREVIEW_TRIPLES
                or used_bytes + size > PREVIEW_BYTES
                or len(nodes.keys() | {source, target}) > PREVIEW_NODES
            ):
                break
            sample.add((subject, predicate, obj))
            used_bytes += size
            for term, key in ((subject, source), (obj, target)):
                label, display, label_source = display_label(term)
                metadata = {
                    "iri": str(term) if isinstance(term, URIRef) else None,
                    "datatype": str(term.datatype) if isinstance(term, Literal) and term.datatype else None,
                    "language": term.language if isinstance(term, Literal) else None,
                }
                if display:
                    metadata.update(
                        {
                            name: display[name]
                            for name in (
                                "ifc_class",
                                "global_id",
                                "description",
                            )
                            if display[name] is not None
                        }
                    )
                if label_source:
                    metadata["label_source"] = label_source
                nodes.setdefault(
                    key,
                    {
                        "id": key,
                        "label": label,
                        "kind": "Literal" if isinstance(term, Literal) else "Resource",
                        "metadata": metadata,
                    },
                )
            edges.append(
                {
                    "id": sha256(" ".join(t.n3() for t in (subject, predicate, obj)).encode()).hexdigest(),
                    "source": source,
                    "target": target,
                    "relation": str(predicate),
                }
            )
        return {
            "ttl": sample.serialize(format="nt"),
            "nodes": list(nodes.values()),
            "edges": edges,
            "total_triples": len(graph),
            "total_nodes": len(total_nodes),
            "total_edges": len(graph),
            "preview_triples": len(sample),
            "truncated": len(sample) < len(graph),
        }


class GraphResolver:
    def __init__(self, db: Session, user: User, *, buildings=None, objects=None):
        self.db = db
        self.user = user
        self.buildings = buildings or BuildingRepository()
        self.objects = objects

    def _publication(self, graph_id):
        publication = self.db.get(PublishedGraph, graph_id, populate_existing=True)
        if publication is None:
            return None
        model = self.db.get(IfcModel, publication.model_id, populate_existing=True)
        if (
            publication.user_id != self.user.id
            or publication.backend != "neo"
            or publication.status != "ready"
            or model is None
            or model.deleted_at is not None
            or model.user_id != self.user.id
            or model.ingestion_backend != "neo"
        ):
            raise HTTPException(404, "Graph not found")
        graph_identifier(graph_id, strict=True)
        if publication.graph_uri != default_building_graph_uri(graph_id):
            raise HTTPException(409, "Invalid graph registration")
        return publication

    def _read(self, reference):
        if (
            not isinstance(reference, dict)
            or not isinstance(reference.get("bytes"), int)
            or not 0 <= reference["bytes"] <= LIMITS.file_bytes
        ):
            raise HTTPException(413, "Graph artifact exceeds the read budget")
        try:
            if self.objects is None:
                self.objects = IngestionObjects.for_worker()
            return self.objects.read_verified(reference["key"], reference["bytes"], reference["sha256"]).decode("utf-8")
        except (AdmissionError, UnicodeError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(503, "Published graph artifact is unavailable or failed verification") from exc

    def preview(self, resolved: ResolvedGraph) -> dict:
        display_index = None
        publication = resolved.publication
        reference = publication.manifest if publication else None
        if reference not in (None, {}):
            if not isinstance(reference, dict) or not reference.get("key"):
                raise HTTPException(503, "Published graph manifest reference is invalid")
            try:
                manifest = json.loads(self._read(reference))
            except (TypeError, ValueError) as exc:
                raise HTTPException(503, "Published graph manifest is invalid") from exc
            if (
                not isinstance(manifest, dict)
                or manifest.get("manifest_version") != "neo-publication/1"
                or manifest.get("graph_id") != publication.id
                or manifest.get("model_id") != publication.model_id
                or manifest.get("job_id") != publication.job_id
                or manifest.get("revision_digest") != publication.revision_digest
            ):
                raise HTTPException(503, "Published graph manifest does not match its registration")
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict):
                raise HTTPException(503, "Published graph manifest artifacts are invalid")
            if "display-index.json" in artifacts:
                display_reference = artifacts["display-index.json"]
                validation_reference = artifacts.get("validation-report.json")
                if not isinstance(display_reference, dict) or not isinstance(validation_reference, dict):
                    raise HTTPException(503, "Published display index has no validation report")
                try:
                    validation = json.loads(self._read(validation_reference))
                    display_index = json.loads(self._read(display_reference))
                    validate_display_index(
                        display_index,
                        validation,
                        publication.model_id,
                        publication.revision_digest,
                    )
                except HTTPException:
                    raise
                except (TypeError, ValueError) as exc:
                    raise HTTPException(503, "Published display index failed verification") from exc
        return resolved.preview(display_index)

    def publication_for(self, value: str) -> PublishedGraph | None:
        explicit_ifc = value.startswith("ifc://")
        graph_id = graph_identifier(value[6:] if explicit_ifc else value)
        publication = self._publication(graph_id)
        if publication and explicit_ifc:
            raise HTTPException(404, "Graph not found")
        return publication

    def resolve(self, value: str, *, prefer_sql: bool = False) -> ResolvedGraph:
        explicit_ifc = value.startswith("ifc://")
        graph_id = graph_identifier(value[6:] if explicit_ifc else value)
        publication = self.publication_for(value)
        if publication:
            ttl = self._read(publication.destination)
            ontologies = []
            context_bytes = 0
            for item in publication.ontology_artifacts:
                reference, pin = item["artifact"], item["pin"]
                if pin.get("sha256") != reference.get("sha256"):
                    raise HTTPException(503, "Retained ontology digest does not match its pin")
                context_bytes += reference["bytes"]
                if context_bytes > CONTEXT_BYTES:
                    raise HTTPException(413, "Retained ontology context exceeds the read budget")
                ontologies.append((pin, self._read(reference)))
            try:
                graph = Graph().parse(data=ttl, format="turtle")
                for _, ontology in ontologies:
                    context = Graph().parse(data=ontology, format="turtle")
                    if len(context) > LIMITS.triples:
                        raise HTTPException(413, "Retained ontology exceeds the triple budget")
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(503, "Invalid published RDF") from exc
            if len(graph) != publication.triple_count or len(graph) > LIMITS.triples:
                raise HTTPException(409, "Published graph triple count does not match its registration")
            return ResolvedGraph(graph_id, publication.graph_uri, "neo", ttl, publication, ontologies)
        if graph_id.startswith("neo_"):
            raise HTTPException(404, "Graph not found")
        model = self.db.get(IfcModel, graph_id, populate_existing=True)
        # A Neo model ID is never an alias for a graph revision, even if an old TTL has the same ID.
        if model and model.ingestion_backend != "legacy":
            raise HTTPException(404, "Select a ready graph revision, not a model ID")
        if (
            prefer_sql
            and not value.startswith(BUILDING_GRAPH_BASE_URI)
            and model
            and model.deleted_at is None
            and model.user_id == self.user.id
        ):
            return ResolvedGraph(graph_id, "ifc://" + graph_id, "legacy-sql")
        if not explicit_ifc:
            try:
                uri, ttl = self.buildings.fetch_graph(graph_id)
                return ResolvedGraph(graph_id, uri, "legacy", ttl)
            except GraphStoreNotFoundError:
                pass
            except GraphStoreError as exc:
                raise HTTPException(502, "Legacy graph storage is unavailable") from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if model and model.ingestion_backend == "legacy" and model.deleted_at is None and model.user_id == self.user.id:
            return ResolvedGraph(graph_id, "ifc://" + graph_id, "legacy-sql")
        if model is None:
            raise LegacyGraphNotFound()
        raise HTTPException(404, "Graph not found")

    def list_graphs(self) -> list[dict]:
        publications = self.db.exec(
            select(PublishedGraph, IfcModel)
            .join(
                IfcModel,
                PublishedGraph.model_id == IfcModel.id,
            )
            .where(
                PublishedGraph.user_id == self.user.id,
                PublishedGraph.backend == "neo",
                PublishedGraph.status == "ready",
                IfcModel.user_id == self.user.id,
                IfcModel.deleted_at.is_(None),
                IfcModel.ingestion_backend == "neo",
            )
        ).all()
        items = []
        for item in self.buildings.list_graphs():
            graph_id = graph_identifier(item["graph_uri"])
            if graph_id.startswith("neo_") or self.db.get(PublishedGraph, graph_id):
                continue
            model = self.db.get(IfcModel, graph_id)
            if model and model.ingestion_backend != "legacy":
                continue
            items.append({**item, "graph_id": graph_id, "backend": "legacy"})
        for model in self.db.exec(
            select(IfcModel).where(
                IfcModel.user_id == self.user.id, IfcModel.deleted_at.is_(None), IfcModel.ingestion_backend == "legacy"
            )
        ).all():
            if not model.id.startswith("neo_") and not self.db.get(PublishedGraph, model.id):
                items.append(
                    {
                        "graph_id": model.id,
                        "graph_uri": "ifc://" + model.id,
                        "label": model.filename,
                        "backend": "legacy-sql",
                    }
                )
        for publication, model in publications:
            items.append(
                {
                    "graph_id": publication.id,
                    "graph_uri": publication.graph_uri,
                    "backend": "neo",
                    "status": "ready",
                    "immutable": True,
                    "model_id": model.id,
                    "profile_id": publication.profile_id,
                    "revision_id": publication.revision_id,
                    "revision_digest": publication.revision_digest,
                    "label": model.filename,
                    "triple_count": publication.triple_count,
                }
            )
        return items

    def require_mutable(self, value: str):
        graph_id = graph_identifier(value)
        publication = self._publication(graph_id)
        model = self.db.get(IfcModel, graph_id)
        if publication or graph_id.startswith("neo_") or model and model.ingestion_backend != "legacy":
            raise HTTPException(409, "Immutable ingestion baselines cannot be written or deleted; use model deletion")
