"""Shared dataclasses for the K8S Insights rule engine.

Findings are POPOs — pure-Python dictionaries returned by every rule. We use
@dataclass for the in-process construction ergonomics, then asdict() at the
boundary so the macro return shape is plain JSON.
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


SEVERITY_ORDER = ('critical', 'high', 'medium', 'low', 'info')
SEVERITY_RANK = {sev: idx for idx, sev in enumerate(SEVERITY_ORDER)}


@dataclass
class Remediation:
    kind: str  # 'kubectl' | 'file-edit' | 'gui-step' | 'doc-link'
    title: str
    body: str = ''  # command, snippet, or step description
    target: Optional[str] = None  # file path / URL / namespace context


@dataclass
class Finding:
    id: str
    rule: str
    severity: str
    category: str
    title: str
    summary: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    remediation: List[Remediation] = field(default_factory=list)
    cost_impact_per_month: Optional[float] = None
    confidence: str = 'high'  # 'high' | 'medium' | 'low'

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['costImpactPerMonth'] = d.pop('cost_impact_per_month')
        d['remediation'] = [asdict(r) if not isinstance(r, dict) else r for r in self.remediation]
        return d


def sort_findings(findings: List[Finding]) -> List[Finding]:
    def key(f: Finding):
        sev = SEVERITY_RANK.get(f.severity, 99)
        cost = -(f.cost_impact_per_month or 0.0)
        return (sev, cost, f.rule, f.id)
    return sorted(findings, key=key)
