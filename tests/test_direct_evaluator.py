from __future__ import annotations

from app.checks.evaluator_direct import DirectAtomicEvaluator


def test_between_operator_uses_exclusive_upper_bound():
    evaluator = DirectAtomicEvaluator()

    assert evaluator._compare(actual=5, operator="between", expected=[1, 10]) is True
    assert evaluator._compare(actual=10, operator="between", expected=[1, 10]) is False
