from unittest.mock import MagicMock, patch

import pytest

from app.checks.evaluator_sparql import SparqlAtomicEvaluator
from app.checks.result_models import DecisionNode, EvalContext, GraphRef


@pytest.fixture
def evaluator():
    return SparqlAtomicEvaluator(endpoint_url="http://mock-ontop/sparql")

@pytest.fixture
def graph_ref():
    return GraphRef(graph_id="test", graph_uri="http://example.org/graph")

@pytest.fixture
def eval_ctx():
    return EvalContext(rule_id="test-rule", graph_id="test", graph_uri="http://example.org/graph")

def test_evaluate_true(evaluator, graph_ref, eval_ctx):
    target = "http://example.org/firebim/wall1"
    node = DecisionNode(
        node_id="node1",
        target_class="IfcWall",
        property_path="height",
        operator=">",
        value=3.0
    )

    # Mock SPARQL response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "bindings": [
                {
                    "value": {
                        "type": "literal",
                        "value": "3.5",
                        "datatype": "http://www.w3.org/2001/XMLSchema#float"
                    }
                }
            ]
        }
    }

    with patch("requests.post", return_value=mock_response):
        outcome = evaluator.evaluate(graph_ref, node, target, eval_ctx)

    assert outcome.status == "True"
    assert outcome.evidence["actual_value"] == "3.5"
    assert outcome.evidence["operator"] == ">"

def test_evaluate_false(evaluator, graph_ref, eval_ctx):
    target = "http://example.org/firebim/wall1"
    node = DecisionNode(
        node_id="node1",
        target_class="IfcWall",
        property_path="height",
        operator=">",
        value=5.0
    )

    # Mock SPARQL response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "bindings": [
                {
                    "value": {
                        "type": "literal",
                        "value": "3.5",
                        "datatype": "http://www.w3.org/2001/XMLSchema#float"
                    }
                }
            ]
        }
    }

    with patch("requests.post", return_value=mock_response):
        outcome = evaluator.evaluate(graph_ref, node, target, eval_ctx)

    assert outcome.status == "False"

def test_evaluate_missing_data(evaluator, graph_ref, eval_ctx):
    target = "http://example.org/firebim/wall1"
    node = DecisionNode(
        node_id="node1",
        target_class="IfcWall",
        property_path="height",
        operator=">",
        value=3.0
    )

    # Mock SPARQL response (no bindings)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "bindings": []
        }
    }

    with patch("requests.post", return_value=mock_response):
        outcome = evaluator.evaluate(graph_ref, node, target, eval_ctx)

    assert outcome.status == "Unknown"
    assert outcome.reason_code == "missing_required_input"

def test_evaluate_many(evaluator, graph_ref, eval_ctx):
    targets = ["wall1", "wall2"]
    node = DecisionNode(
        node_id="node1",
        target_class="IfcWall",
        property_path="height",
        operator=">",
        value=3.0
    )

    # Mock SPARQL response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "bindings": [
                {
                    "target": {"value": "http://example.org/firebim/wall1"},
                    "value": {
                        "type": "literal",
                        "value": "3.5",
                        "datatype": "http://www.w3.org/2001/XMLSchema#float"
                    }
                },
                {
                    "target": {"value": "http://example.org/firebim/wall2"},
                    "value": {
                        "type": "literal",
                        "value": "2.5",
                        "datatype": "http://www.w3.org/2001/XMLSchema#float"
                    }
                }
            ]
        }
    }

    with patch("requests.post", return_value=mock_response):
        outcomes = evaluator.evaluate_many(graph_ref=graph_ref, node=node, targets=targets, ctx=eval_ctx)

    assert len(outcomes) == 2
    assert outcomes["wall1"].status == "True"
    assert outcomes["wall2"].status == "False"
