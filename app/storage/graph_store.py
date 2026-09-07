from __future__ import annotations

import logging
from time import monotonic
from urllib.parse import quote, unquote

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from rdflib import DCTERMS, RDFS, Graph, URIRef

from app.core.config import settings
from app.core.observability import is_request_profiling_enabled, log_event, record_metric

logger = logging.getLogger(__name__)


class GraphStoreError(Exception):
    pass


class GraphStoreNotFoundError(GraphStoreError):
    pass


class GraphStoreClient:
    ROOT_PREFIX = "graphs"

    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region_name: str | None = None,
        timeout: float = 30.0,
        s3_client=None,
    ) -> None:
        self.bucket_name = bucket_name or settings.S3_BUCKET_NAME
        self.prefix = self.ROOT_PREFIX
        self.timeout = timeout
        self.client = s3_client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or settings.S3_ENDPOINT_URL,
            aws_access_key_id=access_key_id or settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=(secret_access_key or settings.S3_SECRET_ACCESS_KEY.get_secret_value()),
            region_name=region_name or settings.S3_REGION,
        )

    def _dataset_prefix(self, dataset: str) -> str:
        dataset = str(dataset).strip().strip("/")
        if not dataset:
            raise ValueError("dataset must not be empty")
        return f"{self.prefix}/{dataset}/"

    def _object_key(self, dataset: str, graph_uri: str) -> str:
        graph_uri = str(graph_uri).strip()
        if not graph_uri:
            raise ValueError("graph_uri must not be empty")
        encoded = quote(graph_uri, safe="")
        return f"{self._dataset_prefix(dataset)}{encoded}.ttl"

    def _graph_uri_from_key(self, key: str) -> str:
        _, _, encoded = key.partition(self.prefix + "/")
        if not encoded:
            raise ValueError(f"Unexpected graph store key: {key}")
        _, _, encoded = encoded.partition("/")
        if not encoded.endswith(".ttl"):
            raise ValueError(f"Unexpected graph store key suffix: {key}")
        return unquote(encoded[:-4])

    @staticmethod
    def _triple_count(ttl: str) -> int:
        return len(GraphStoreClient._parse(ttl))

    @staticmethod
    def _parse(ttl: str) -> Graph:
        graph = Graph()
        try:
            graph.parse(data=ttl, format="turtle")
        except Exception as exc:
            raise ValueError(f"Invalid Turtle content: {exc}") from exc
        return graph

    @staticmethod
    def _self_description(graph: Graph, graph_uri: str) -> dict[str, str]:
        """`label` / `description` a graph states about itself (rdfs:label / rdfs:comment / dcterms:* on the graph URI).

        Stored as object metadata so a listing can show what a graph is for without fetching it. S3 metadata must be
        ASCII and small, so the text is percent-encoded and capped."""
        subject = URIRef(graph_uri)
        found: dict[str, str] = {}
        # S3 allows 2 KB of user metadata per object in total; graph-uri and triple-count take a little of it.
        # The caps apply to the *encoded* text (non-ASCII becomes %XX%XX…), cut at a whole character.
        for key, predicates, limit in (
            ("label", (RDFS.label, DCTERMS.title), 240),
            ("description", (RDFS.comment, DCTERMS.description), 1400),
        ):
            for predicate in predicates:
                value = graph.value(subject, predicate)
                if value is not None and str(value).strip():
                    found[key] = GraphStoreClient._encode_capped(str(value).strip(), limit)
                    break
        return found

    @staticmethod
    def _encode_capped(text: str, limit: int) -> str:
        encoded = quote(text, safe="")
        if len(encoded) <= limit:
            return encoded
        ellipsis = quote("…", safe="")
        out = ""
        for char in text:
            piece = quote(char, safe="")
            if len(out) + len(piece) + len(ellipsis) > limit:
                break
            out += piece
        return out + ellipsis

    @staticmethod
    def _decode_self_description(metadata: dict) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for key in ("label", "description"):
            raw = metadata.get(key)
            out[key] = unquote(raw) if raw else None
        return out

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        return code in {"404", "NoSuchKey", "NotFound"}

    def ping_dataset(self, dataset: str) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=self._dataset_prefix(dataset),
                MaxKeys=1,
            )
            return True
        except (BotoCoreError, ClientError):
            return False

    def put_graph_ttl(
        self,
        dataset: str,
        graph_uri: str,
        ttl: str,
        *,
        triple_count: int | None = None,
    ) -> None:
        graph = self._parse(ttl)
        if triple_count is None:
            triple_count = len(graph)
        key = self._object_key(dataset, graph_uri)
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=ttl.encode("utf-8"),
                ContentType="text/turtle; charset=utf-8",
                Metadata={
                    "graph-uri": graph_uri,
                    "triple-count": str(triple_count),
                    **self._self_description(graph, graph_uri),
                },
            )
        except (BotoCoreError, ClientError) as exc:
            raise GraphStoreError(f"Graph store publish failed: {exc}") from exc

    def get_graph_ttl(self, dataset: str, graph_uri: str) -> str:
        started_at = monotonic()
        key = self._object_key(dataset, graph_uri)
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            if self._is_not_found(exc):
                raise GraphStoreNotFoundError(f"Graph not found: {graph_uri}") from exc
            raise GraphStoreError(f"Graph store fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise GraphStoreError(f"Graph store fetch failed: {exc}") from exc

        body = response["Body"].read()
        ttl = body.decode("utf-8")
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        record_metric("graph_store.get_graph_ttl_ms", duration_ms, append=True)
        if is_request_profiling_enabled():
            log_event(
                logger,
                "graph_store.get_graph_ttl",
                dataset=dataset,
                graph_uri=graph_uri,
                duration_ms=duration_ms,
                byte_size=len(body),
            )
        return ttl

    def delete_graph(self, dataset: str, graph_uri: str) -> None:
        key = self._object_key(dataset, graph_uri)
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            if self._is_not_found(exc):
                raise GraphStoreNotFoundError(f"Graph not found: {graph_uri}") from exc
            raise GraphStoreError(f"Graph store delete failed: {exc}") from exc
        except BotoCoreError as exc:
            raise GraphStoreError(f"Graph store delete failed: {exc}") from exc

        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise GraphStoreError(f"Graph store delete failed: {exc}") from exc

    def list_named_graphs(self, dataset: str, base_uri: str) -> list[dict]:
        items: list[dict] = []
        continuation_token: str | None = None
        prefix = self._dataset_prefix(dataset)

        while True:
            kwargs = {
                "Bucket": self.bucket_name,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            try:
                response = self.client.list_objects_v2(**kwargs)
            except (BotoCoreError, ClientError) as exc:
                raise GraphStoreError(f"Graph store list failed: {exc}") from exc

            for obj in response.get("Contents", []):
                key = obj["Key"]
                graph_uri = self._graph_uri_from_key(key)
                if not graph_uri.startswith(base_uri):
                    continue

                # list_objects_v2 does not return user metadata; one HEAD per graph gives count, label, description
                try:
                    head = self.client.head_object(Bucket=self.bucket_name, Key=key)
                except (BotoCoreError, ClientError) as exc:
                    raise GraphStoreError(f"Graph store list failed: {exc}") from exc
                metadata = head.get("Metadata") or {}
                triple_count = None
                raw_count = metadata.get("triple-count")
                if raw_count is not None:
                    try:
                        triple_count = int(raw_count)
                    except (TypeError, ValueError):
                        triple_count = None

                items.append(
                    {
                        "graph_uri": graph_uri,
                        "triple_count": triple_count,
                        **self._decode_self_description(metadata),
                    }
                )

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return sorted(items, key=lambda item: item["graph_uri"])
