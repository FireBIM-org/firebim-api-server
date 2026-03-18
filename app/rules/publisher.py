import httpx
from urllib.parse import urlencode

from app.core.config import settings


def default_ruleset_graph_uri(ruleset_id: str) -> str:
    return f"https://firebim.org/graphs/rules/{ruleset_id}"


def publish_ttl_to_fuseki(ttl_text: str, dataset: str, graph_uri: str) -> None:
    """
    Publish Turtle to a named graph in Fuseki using Graph Store Protocol.
    """
    base = settings.FUSEKI_HOST.rstrip("/")
    endpoint = f"{base}/{dataset}/data?{urlencode({'graph': graph_uri})}"

    auth = None
    if settings.FUSEKI_USER and settings.FUSEKI_PASSWORD:
        auth = (settings.FUSEKI_USER, settings.FUSEKI_PASSWORD)

    print("FUSEKI_HOST =", settings.FUSEKI_HOST)
    print("FUSEKI_USER =", settings.FUSEKI_USER)
    print("FUSEKI_PASSWORD_SET =", bool(settings.FUSEKI_PASSWORD))
    print("FUSEKI_ENDPOINT =", endpoint)

    response = httpx.put(
        endpoint,
        content=ttl_text.encode("utf-8"),
        headers={"Content-Type": "text/turtle"},
        auth=auth,
        timeout=30.0,
    )

    if response.status_code not in (200, 201, 204):
        raise ValueError(
            f"Fuseki publish failed: {response.status_code} {response.text}"
        )
    
def fetch_ttl_from_fuseki(dataset: str, graph_uri: str) -> str:
    """
    Fetch Turtle from a named graph in Fuseki using Graph Store Protocol.
    """
    base = settings.FUSEKI_HOST.rstrip("/")
    endpoint = f"{base}/{dataset}/data?{urlencode({'graph': graph_uri})}"

    auth = None
    if settings.FUSEKI_USER and settings.FUSEKI_PASSWORD:
        auth = (settings.FUSEKI_USER, settings.FUSEKI_PASSWORD)

    response = httpx.get(
        endpoint,
        headers={"Accept": "text/turtle"},
        auth=auth,
        timeout=30.0,
    )

    if response.status_code != 200:
        raise ValueError(
            f"Fuseki fetch failed: {response.status_code} {response.text}"
        )

    return response.text