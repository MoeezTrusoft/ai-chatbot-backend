"""Funnel rule governance helpers.

D-081 moves funnel-stage classification into Tri-Match in shadow mode. This
package remains as the rule governance boundary for imported funnel material:
it partitions user-language rules from CRM/internal rules and only converts the
user-language subset into Tri-Match-compatible funnel-stage rules.
"""

from .partitioner import (
    FunnelRulePartitioner,
    load_funnel_source,
    partition_source,
    verify_funnel_partition,
)
from .schemas import (
    DroppedFunnelRule,
    FunnelPartition,
    FunnelPartitionReport,
    FunnelRawRule,
)

__all__ = [
    "DroppedFunnelRule",
    "FunnelPartition",
    "FunnelPartitionReport",
    "FunnelRawRule",
    "FunnelRulePartitioner",
    "load_funnel_source",
    "partition_source",
    "verify_funnel_partition",
]
