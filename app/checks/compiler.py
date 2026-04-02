from __future__ import annotations

import re
from typing import Any

from rdflib import Graph, Namespace, RDF
from rdflib.namespace import SH

from app.checks.result_models import (
    CompiledRuleSet,
    DecisionNode,
    EndNode,
    StartNode,
)

EX = Namespace("http://example.org/firebim/")
FB = Namespace("http://example.org/firebim/rule/")


class RuleCompiler:
    """
    Compile FireBIM SHACL TTL into a deterministic execution graph.

    Design goals:
    - stay compatible with the current RuleSet / RuleElement TTL structure
    - compile both decision nodes (SHACL NodeShape) and metadata nodes (start/end)
    - validate execution graph completeness enough for runtime traversal
    - preserve raw source TTL for later debugging/explaining
    """

    def compile_from_ttl(self, rule_ttl: str) -> CompiledRuleSet:
        if not rule_ttl or not str(rule_ttl).strip():
            raise ValueError("Rule TTL is empty")

        graph = Graph()
        graph.parse(data=rule_ttl, format="turtle")

        ruleset_subjects = list(graph.subjects(RDF.type, EX.RuleSet))
        if not ruleset_subjects:
            raise ValueError("No RuleSet resource found in TTL")

        ruleset_uri = ruleset_subjects[0]
        rule_id = self._local_name(ruleset_uri)

        entry_obj = graph.value(ruleset_uri, EX.entryPoint)
        entry_node_id = self._local_name(entry_obj) if entry_obj else None
        if not entry_node_id:
            raise ValueError(f"Rule '{rule_id}' is missing ex:entryPoint")

        compiled_nodes: dict[str, StartNode | DecisionNode | EndNode] = {}

        # 1) compile decision nodes from SHACL NodeShapes
        for shape in graph.subjects(RDF.type, SH.NodeShape):
            node = self._compile_decision_node(graph, shape)
            if node.node_id in compiled_nodes:
                raise ValueError(f"Duplicate node id detected: '{node.node_id}'")
            compiled_nodes[node.node_id] = node

        # 2) compile metadata nodes (typically start/end) from EX.RuleElement,
        #    but skip anything already represented as a SHACL NodeShape
        for node_uri in graph.subjects(RDF.type, EX.RuleElement):
            node_id = self._local_name(node_uri)
            if node_id in compiled_nodes:
                continue

            compiled = self._compile_metadata_node(graph, node_uri)
            if compiled.node_id in compiled_nodes:
                raise ValueError(f"Duplicate node id detected: '{compiled.node_id}'")
            compiled_nodes[compiled.node_id] = compiled

        compiled = CompiledRuleSet(
            rule_id=rule_id,
            entry_node_id=entry_node_id,
            nodes=compiled_nodes,
            source_rule_ttl=rule_ttl,
            source_graph_uri=str(ruleset_uri),
            metadata={
                "compiler": "RuleCompiler",
                "node_count": len(compiled_nodes),
                "decision_count": sum(
                    1 for n in compiled_nodes.values() if n.node_type == "decision"
                ),
                "start_count": sum(
                    1 for n in compiled_nodes.values() if n.node_type == "start"
                ),
                "end_count": sum(
                    1 for n in compiled_nodes.values() if n.node_type == "end"
                ),
            },
        )

        self._validate_compiled_ruleset(compiled)
        return compiled

    def _compile_decision_node(self, graph: Graph, shape_uri) -> DecisionNode:
        node_id = self._local_name(shape_uri)

        target_obj = graph.value(shape_uri, SH.targetClass)
        target_class = str(target_obj) if target_obj is not None else None

        true_obj = graph.value(shape_uri, FB.truePath)
        false_obj = graph.value(shape_uri, FB.falsePath)
        unknown_obj = graph.value(shape_uri, FB.unknownPath)

        rule_bnode = graph.value(shape_uri, SH.rule)
        construct_text = graph.value(rule_bnode, SH.construct) if rule_bnode else None

        property_path = None
        operator = None
        value: Any = None

        if construct_text:
            property_path, operator, value = self._parse_construct_rule(
                str(construct_text)
            )

        label = graph.value(shape_uri, EX.label)

        config: dict[str, Any] = {}
        if construct_text:
            config["construct"] = str(construct_text)

        node = DecisionNode(
            node_id=node_id,
            label=str(label) if label is not None else None,
            target_class=target_class,
            property_path=property_path,
            operator=operator,
            value=value,
            true_node_id=self._local_name(true_obj) if true_obj else None,
            false_node_id=self._local_name(false_obj) if false_obj else None,
            unknown_node_id=self._local_name(unknown_obj) if unknown_obj else None,
            shape_id=node_id,
            shape_uri=str(shape_uri),
            sparql_construct=str(construct_text)
            if construct_text is not None
            else None,
            config=config,
        )

        self._validate_decision_node(node)
        return node

    def _compile_metadata_node(self, graph: Graph, node_uri) -> StartNode | EndNode:
        node_id = self._local_name(node_uri)

        node_type_obj = graph.value(node_uri, EX.nodeType)
        node_type = str(node_type_obj) if node_type_obj is not None else None
        if not node_type:
            raise ValueError(f"Rule metadata node '{node_id}' is missing ex:nodeType")

        label = graph.value(node_uri, EX.label)
        value = graph.value(node_uri, EX.value)

        true_obj = graph.value(node_uri, FB.truePath)
        false_obj = graph.value(node_uri, FB.falsePath)
        unknown_obj = graph.value(node_uri, FB.unknownPath)

        end_kind = graph.value(node_uri, EX.endKind)
        output_text = graph.value(node_uri, EX.outputText)

        config: dict[str, Any] = {}
        if value is not None:
            config["value"] = str(value)
        if end_kind is not None:
            config["end_kind"] = str(end_kind)
        if output_text is not None:
            config["output_text"] = str(output_text)

        if node_type == "start":
            node = StartNode(
                node_id=node_id,
                label=str(label) if label is not None else None,
                next_node_id=self._local_name(true_obj) if true_obj else None,
                config=config,
            )
            self._validate_start_node(
                node, false_obj=false_obj, unknown_obj=unknown_obj
            )
            return node

        if node_type == "end":
            node = EndNode(
                node_id=node_id,
                label=str(label) if label is not None else None,
                end_kind=str(end_kind) if end_kind is not None else None,
                output_text=str(output_text) if output_text is not None else None,
                config=config,
            )
            self._validate_end_node(
                node,
                true_obj=true_obj,
                false_obj=false_obj,
                unknown_obj=unknown_obj,
            )
            return node

        raise ValueError(
            f"Rule metadata node '{node_id}' has unsupported node_type '{node_type}'. "
            "Expected 'start' or 'end'."
        )

    def _validate_compiled_ruleset(self, compiled: CompiledRuleSet) -> None:
        if not compiled.entry_node_id:
            raise ValueError(f"Rule '{compiled.rule_id}' is missing entry node")

        if compiled.entry_node_id not in compiled.nodes:
            raise ValueError(
                f"Rule '{compiled.rule_id}' entry node '{compiled.entry_node_id}' "
                "does not exist in compiled nodes"
            )

        entry_node = compiled.nodes[compiled.entry_node_id]
        if entry_node.node_type not in {"start", "decision"}:
            raise ValueError(
                f"Rule '{compiled.rule_id}' entry node '{compiled.entry_node_id}' "
                f"must be a start or decision node, got '{entry_node.node_type}'"
            )

        if not compiled.nodes:
            raise ValueError(f"Rule '{compiled.rule_id}' compiled to zero nodes")

        referenced_node_ids: set[str] = set()

        for node in compiled.nodes.values():
            if node.node_type == "start":
                if node.next_node_id:
                    referenced_node_ids.add(node.next_node_id)

            elif node.node_type == "decision":
                for next_id in [
                    node.true_node_id,
                    node.false_node_id,
                    node.unknown_node_id,
                ]:
                    if next_id:
                        referenced_node_ids.add(next_id)

            elif node.node_type == "end":
                pass

        missing = sorted(
            node_id for node_id in referenced_node_ids if node_id not in compiled.nodes
        )
        if missing:
            raise ValueError(
                f"Rule '{compiled.rule_id}' contains branch targets that do not exist: {missing}"
            )

        end_nodes = [
            node.node_id for node in compiled.nodes.values() if node.node_type == "end"
        ]
        if not end_nodes:
            raise ValueError(
                f"Rule '{compiled.rule_id}' must contain at least one end node"
            )

        # optional: detect unreachable nodes
        reachable = self._reachable_node_ids(compiled)
        unreachable = sorted(
            node_id for node_id in compiled.nodes if node_id not in reachable
        )
        if unreachable:
            compiled.metadata["unreachable_nodes"] = unreachable

    def _reachable_node_ids(self, compiled: CompiledRuleSet) -> set[str]:
        seen: set[str] = set()
        stack: list[str] = [compiled.entry_node_id]

        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)

            node = compiled.nodes.get(current)
            if node is None:
                continue

            next_ids: list[str] = []

            if node.node_type == "start":
                if node.next_node_id:
                    next_ids.append(node.next_node_id)

            elif node.node_type == "decision":
                for next_id in [
                    node.true_node_id,
                    node.false_node_id,
                    node.unknown_node_id,
                ]:
                    if next_id:
                        next_ids.append(next_id)

            for next_id in next_ids:
                if next_id not in seen:
                    stack.append(next_id)

        return seen

    def _validate_decision_node(self, node: DecisionNode) -> None:
        missing = [
            name
            for name, value in [
                ("target_class", node.target_class),
                ("property_path", node.property_path),
                ("operator", node.operator),
            ]
            if not value
        ]
        if missing:
            raise ValueError(
                f"Decision node '{node.node_id}' missing required fields: {', '.join(missing)}"
            )

        missing_branches = [
            name
            for name, value in [
                ("true_node_id", node.true_node_id),
                ("false_node_id", node.false_node_id),
                ("unknown_node_id", node.unknown_node_id),
            ]
            if not value
        ]
        if missing_branches:
            raise ValueError(
                f"Decision node '{node.node_id}' missing required outgoing branches: "
                f"{', '.join(missing_branches)}"
            )

    def _validate_start_node(
        self, node: StartNode, false_obj=None, unknown_obj=None
    ) -> None:
        if false_obj is not None or unknown_obj is not None:
            raise ValueError(
                f"Start node '{node.node_id}' should not define false_path or unknown_path"
            )

        if not node.next_node_id:
            raise ValueError(f"Start node '{node.node_id}' is missing next node")

    def _validate_end_node(
        self, node: EndNode, true_obj=None, false_obj=None, unknown_obj=None
    ) -> None:
        if true_obj is not None or false_obj is not None or unknown_obj is not None:
            raise ValueError(
                f"End node '{node.node_id}' should not define outgoing paths"
            )

    def _local_name(self, uri) -> str:
        uri = str(uri)
        if "#" in uri:
            return uri.rsplit("#", 1)[-1]
        return uri.rstrip("/").rsplit("/", 1)[-1]

    def _parse_construct_rule(
        self, sparql_text: str
    ) -> tuple[str | None, str | None, Any]:
        """
        Parse the current SHACLBuilder-style construct SPARQL into a lightweight
        executable decision descriptor.

        Supported today:
        - OPTIONAL { ?this <prop> ?h }
        - IF (?h <=|>=|=|<|> value, fb:True, ...)
        - numeric values
        - quoted string values
        - fallback raw value text

        This intentionally mirrors the current decompile logic so v2 stays aligned
        with your existing rules toolchain.
        """
        prop_match = re.search(
            r"OPTIONAL\s*\{\s*\?this\s*<([^>]+)>\s*\?h\s*\}",
            sparql_text,
            re.DOTALL,
        )
        cmp_match = re.search(
            r"IF\s*\(\s*\?h\s*(<=|>=|=|<|>)\s*(.+?)\s*,\s*fb:True",
            sparql_text,
            re.DOTALL,
        )

        property_path = prop_match.group(1) if prop_match else None
        operator = None
        value: Any = None

        if cmp_match:
            operator = cmp_match.group(1)
            raw_value = cmp_match.group(2).strip().rstrip(") ").strip()

            if re.fullmatch(r"-?\d+", raw_value):
                value = int(raw_value)
            elif re.fullmatch(r"-?\d+\.\d+", raw_value):
                value = float(raw_value)
            elif raw_value.startswith('"') and raw_value.endswith('"'):
                value = raw_value[1:-1]
                if operator == "=":
                    operator = "=="
            else:
                value = raw_value
                if operator == "=":
                    operator = "=="

        return property_path, operator, value
