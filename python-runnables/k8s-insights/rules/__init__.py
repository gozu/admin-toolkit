"""Rule registry.

Adding a rule = appending one entry to ALL_RULES. Each rule subclasses
`Rule` (declared in rules.base) and implements `evaluate(probes) -> list[Finding]`.
"""
from .base import Rule, ProbeBundle  # noqa: F401
from . import connectivity
from . import scheduling
from . import autoscaler
from . import cost
from . import dss_drift
from . import gpu_usage
from . import lifecycle
from . import health


ALL_RULES = [
    *connectivity.RULES,
    *scheduling.RULES,
    *autoscaler.RULES,
    *cost.RULES,
    *dss_drift.RULES,
    *gpu_usage.RULES,
    *lifecycle.RULES,
    *health.RULES,
]
