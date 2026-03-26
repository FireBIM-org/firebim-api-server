import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from rdflib import Graph
from rdflib.plugins.stores.sparqlstore import SPARQLStore

from app.core.config import settings

router = APIRouter(prefix="/graph", tags=["graph"])

# Settings
FUSEKI_HOST = settings.FUSEKI_HOST
# TODO: Warning! Currently using unsecured Fuseki without authentication and no SSL!

# Map country code to Fuseki dataset name
DATASET_BY_COUNTRY: dict[str, str] = {
    "DK": "Danish_dataset",
    "NL": "Dutch_dataset",
    "PT": "Portugal_dataset",
}


# Allow safe paragraph id pattern (avoid SPARQL injection)
PARAGRAPH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _candidate_query_endpoints(dataset: str) -> list[str]:
    """Return possible SPARQL query endpoints for a dataset.
    We try /sparql then /query for broader compatibility.
    """
    return [
        f"{FUSEKI_HOST}/{dataset}/sparql",
        f"{FUSEKI_HOST}/{dataset}/query",
    ]


def _run_sparql(query: str, country_code: str) -> Any:
    ds = DATASET_BY_COUNTRY.get(country_code.upper())
    if not ds:
        raise HTTPException(
            status_code=400, detail=f"Unsupported country_code. Use one of: {', '.join(DATASET_BY_COUNTRY.keys())}"
        )

    last_error: Exception | None = None
    for endpoint in _candidate_query_endpoints(ds):
        try:
            store = SPARQLStore(endpoint)
            g = Graph(store=store)
            result = g.query(query)
            # Try returning standard SPARQL JSON format
            try:
                serialized = result.serialize(format="json")
                # result.serialize may return bytes
                if isinstance(serialized, (bytes, bytearray)):
                    return json.loads(serialized.decode("utf-8"))
                return json.loads(serialized)
            except Exception:
                # Fallback: turn into a simple list of dicts
                rows = []
                for b in result.bindings:
                    row = {str(k): _term_to_json(v) for k, v in b.items()}
                    rows.append(row)
                return {"results": rows}
        except Exception as e:
            last_error = e
            continue

    # If all endpoints failed
    raise HTTPException(status_code=502, detail=f"SPARQL query failed for dataset '{ds}'. Last error: {last_error}")


def _term_to_json(term):
    """Best-effort conversion of an rdflib term to JSON-serializable value."""
    try:
        from rdflib.term import BNode, Literal, URIRef
    except Exception:
        return str(term)

    if term is None:
        return None
    if isinstance(term, Literal):
        d: dict[str, Any] = {"type": "literal", "value": str(term)}
        if term.datatype:
            d["datatype"] = str(term.datatype)
        if term.language:
            d["xml:lang"] = term.language
        return d
    if isinstance(term, URIRef):
        return {"type": "uri", "value": str(term)}
    if isinstance(term, BNode):
        return {"type": "bnode", "value": str(term)}
    return str(term)


@router.get("/count_fro/{country_code}")
def count_fro(country_code: str):
    """
    Count number of entities per FRO class.
    """

    # Query string
    query = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX fbr: <http://www.FireBIM.org/regulations#>
PREFIX fro: <http://www.FireBIM.org/ontologies/FRO#>

SELECT ?class (COUNT(DISTINCT ?x) AS ?count)
WHERE {
  { ?x a fro:Document  . BIND("Document"  AS ?class) }
  UNION
  { ?x a fro:Chapter   . BIND("Chapter"   AS ?class) }
  UNION
  { ?x a fro:Paragraph . BIND("Paragraph" AS ?class) }
  UNION
  { ?x a fro:Section   . BIND("Section"   AS ?class) }
  UNION
  { ?x a fro:Article   . BIND("Article"   AS ?class) }
  UNION
  { ?x a fro:Clause    . BIND("Clause"    AS ?class) }
  UNION
  { ?x a fro:Member    . BIND("Member"    AS ?class) }
}
GROUP BY ?class
ORDER BY ?class
"""
    return _run_sparql(query, country_code)


@router.get("/get_english/{country_code}")
def get_english(country_code: str):
    """
    Get all entities with English original text.
    """

    # Query string
    query = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX fbr: <http://www.FireBIM.org/regulations#>
PREFIX fro: <http://www.FireBIM.org/ontologies/FRO#>

SELECT ?entity ?type ?id ?text_en
WHERE {
  ?entity a ?type ;
          fro:hasID ?id ;
          fro:hasOriginalText ?text_en .
  FILTER(LANG(?text_en) = "en")
  FILTER(?type IN (fro:Paragraph, fro:Section, fro:Clause, fro:Article, fro:Member))
}
ORDER BY ?type ?id
"""
    return _run_sparql(query, country_code)


@router.get("/select_paragraph/{country_code}/{paragraph_id}")
def select_paragraph(country_code: str, paragraph_id: str):
    """ """

    # Sanitize paragraph id to avoid injection, allow e.g. para-82
    if not PARAGRAPH_ID_PATTERN.match(paragraph_id):
        raise HTTPException(status_code=400, detail="Invalid paragraph_id format")

    # Query string
    query = f"""
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX fbr: <http://www.FireBIM.org/regulations#>
PREFIX fro: <http://www.FireBIM.org/ontologies/FRO#>

SELECT ?section ?secId ?secText_da ?secText_en ?clause ?clId ?clText_da ?clText_en
WHERE {{
  ?paragraph a fro:Paragraph ; fro:hasID ?parId .
  FILTER(?parId = \"{paragraph_id}\")

  # sections
  ?paragraph fro:hasSection ?section .
  ?section fro:hasID ?secId .
  OPTIONAL {{ ?section fro:hasOriginalText ?st_da . FILTER(LANG(?st_da)="da") }}
  OPTIONAL {{ ?section fro:hasOriginalText ?st_en . FILTER(LANG(?st_en)="en") }}
  BIND(?st_da AS ?secText_da)
  BIND(?st_en AS ?secText_en)

  # clauses
  OPTIONAL {{
    ?section fro:hasClause ?clause .
    ?clause fro:hasID ?clId .
    OPTIONAL {{ ?clause fro:hasOriginalText ?ct_da . FILTER(LANG(?ct_da)="da") }}
    OPTIONAL {{ ?clause fro:hasOriginalText ?ct_en . FILTER(LANG(?ct_en)="en") }}
    BIND(?ct_da AS ?clText_da)
    BIND(?ct_en AS ?clText_en)
  }}
}}
ORDER BY ?secId ?clId
"""
    return _run_sparql(query, country_code)
