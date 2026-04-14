from __future__ import annotations

from tests.generated_data_ops import purge_generated_rulebook_data, should_prune_generated_data

# Backward-compatible aliases for older local scripts.
should_cleanup_generated_data = should_prune_generated_data
cleanup_generated_rulebook = purge_generated_rulebook_data
