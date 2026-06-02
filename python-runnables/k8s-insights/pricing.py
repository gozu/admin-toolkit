"""EC2 instance pricing — thin wrapper over the live AWS Pricing API.

`hourly_cost` raises `PricingSourceError` on any failure. The audit
top-level resolves prices once into a `price_by_type` table and threads
it through the probe bundle; rules read from that table (not from this
module directly), so a missing/failed pricing source surfaces as
`pricingStatus.ok = False` on the envelope and the cost rules silently
skip via `requires_probes` on the `_pricing` virtual probe.
"""
from pricing_source import (  # type: ignore  # noqa: F401
    PricingSourceError,
    SOURCE_NAME,
    get_on_demand_usd_per_hour,
)


def hourly_cost(instance_type: str, region: str = 'us-west-2') -> float:
    """Return the hourly USD on-demand cost for `instance_type` in `region`.

    Raises PricingSourceError when the source is unavailable. Most call
    sites should read from the audit's `price_by_type` table instead —
    this entry point exists for the small number of code paths (e.g.
    binpack sort) that still need an ad-hoc lookup.
    """
    return get_on_demand_usd_per_hour(instance_type, region)


def monthly_cost(instance_type: str, region: str = 'us-west-2') -> float:
    """Hourly * 730 (avg hours per month)."""
    return hourly_cost(instance_type, region) * 730.0


def is_gpu_instance(instance_type: str) -> bool:
    """Heuristic — instance families that ship with NVIDIA GPUs."""
    if not instance_type:
        return False
    family = instance_type.split('.', 1)[0].lower()
    return family in {'g4dn', 'g5', 'g5g', 'g6', 'g6e', 'p3', 'p4d', 'p4de', 'p5', 'p5e'}
