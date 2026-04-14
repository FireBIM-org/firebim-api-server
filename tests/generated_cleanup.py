from __future__ import annotations

from tests.generated_data_ops import (
    delete_rule,
    delete_rule_element,
    list_generated_rule_elements,
    list_generated_rules,
    purge_generated_rulebook_data,
    should_prune_generated_data,
)

# Backward-compatible aliases for older local scripts.
should_cleanup_generated_data = should_prune_generated_data
cleanup_generated_rulebook = purge_generated_rulebook_data
