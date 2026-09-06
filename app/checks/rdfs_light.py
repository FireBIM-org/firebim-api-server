"""Deterministic, cheap RDFS(-ish) closure on a data graph, applied once per parsed graph.

What it adds (and nothing else):
  * (s rdf:type C)         for every superclass C of a declared class, following rdfs:subClassOf and
                           owl:equivalentClass (both directions)
  * (s P o)                for every superproperty P of a used property, following rdfs:subPropertyOf and
                           owl:equivalentProperty (both directions)
  * (o P s)                for owl:inverseOf pairs, so `fbo:partOf` is derivable from `fbo:hasSubitem`

The schema triples must be in the graph already: the API merges the schema subset of the published ontologies
(see app.api.checks.merge_schema_graphs) before the graph reaches the runners. With no schema triples this is a
no-op. Because it runs inside `CheckExecutionCache.get_or_parse_graph` and the blind runner's parse, every
evaluator, the target index and the SPARQL engine see the same graph.
"""

from __future__ import annotations

from collections import defaultdict

from rdflib import OWL, RDF, RDFS, Graph, URIRef

SCHEMA_PREDICATES = (RDFS.subClassOf, RDFS.subPropertyOf, OWL.equivalentClass, OWL.equivalentProperty, OWL.inverseOf)


def _closure(edges: dict[URIRef, set[URIRef]]) -> dict[URIRef, set[URIRef]]:
    """Transitive closure of a parent relation (node -> all ancestors, excluding itself unless cyclic)."""
    result: dict[URIRef, set[URIRef]] = {}
    for start in edges:
        seen: set[URIRef] = set()
        stack = list(edges[start])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(edges.get(cur, ()))
        result[start] = seen
    return result


Closure = dict[URIRef, set[URIRef]]


def schema_closures(graph: Graph) -> tuple[Closure, Closure, Closure]:
    """-> (class -> superclasses, property -> superproperties, property -> inverse properties)."""
    class_parents: dict[URIRef, set[URIRef]] = defaultdict(set)
    prop_parents: dict[URIRef, set[URIRef]] = defaultdict(set)
    inverses: dict[URIRef, set[URIRef]] = defaultdict(set)
    for s, o in graph.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            class_parents[s].add(o)
    for s, o in graph.subject_objects(OWL.equivalentClass):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            class_parents[s].add(o)
            class_parents[o].add(s)
    for s, o in graph.subject_objects(RDFS.subPropertyOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            prop_parents[s].add(o)
    for s, o in graph.subject_objects(OWL.equivalentProperty):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            prop_parents[s].add(o)
            prop_parents[o].add(s)
    for s, o in graph.subject_objects(OWL.inverseOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            inverses[s].add(o)
            inverses[o].add(s)
    return _closure(class_parents), _closure(prop_parents), dict(inverses)


def materialize(graph: Graph) -> int:
    """Add the inferred triples in place. Returns the number of triples added."""
    class_up, prop_up, inverses = schema_closures(graph)
    if not class_up and not prop_up and not inverses:
        return 0
    added = 0
    new_triples = []
    for s, o in graph.subject_objects(RDF.type):
        for parent in class_up.get(o, ()):
            if parent != o:
                new_triples.append((s, RDF.type, parent))
    for prop in list(prop_up) + list(inverses):
        for s, o in graph.subject_objects(prop):
            for parent in prop_up.get(prop, ()):
                if parent != prop:
                    new_triples.append((s, parent, o))
            for inv in inverses.get(prop, ()):
                new_triples.append((o, inv, s))
    for triple in new_triples:
        if triple not in graph:
            graph.add(triple)
            added += 1
    return added


def schema_subset(ttl: str) -> Graph:
    """The schema triples (and only those) of an ontology TTL, for merging into a building graph."""
    full = Graph()
    full.parse(data=ttl, format="turtle")
    out = Graph()
    for pred in SCHEMA_PREDICATES:
        for s, o in full.subject_objects(pred):
            out.add((s, pred, o))
    return out
