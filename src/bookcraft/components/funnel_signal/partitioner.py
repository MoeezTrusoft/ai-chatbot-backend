from __future__ import annotations

import json
import re
from pathlib import Path

from bookcraft.components.trimatch.schemas import RulePack, RuleTarget, TriMatchLayer, TriMatchRule

from .schemas import DroppedFunnelRule, FunnelPartitionReport, FunnelRawRule

USER_LANGUAGE_MARKERS = (
    "inbound",
    "chat",
    "buyer",
    "message",
    "user language",
    "prospect",
    "customer says",
)
CRM_MARKERS = (
    "crm",
    "internal",
    "opportunity",
    "lifecycle",
    "pipeline",
    "lead score",
)
METADATA_MARKERS = (
    "metadata",
    "session length",
    "message count",
    "utm",
    "referrer",
    "cookie",
)
FORBIDDEN_DECISION_TERMS = (
    "payment risk",
    "legal readiness",
    "discount",
    "quote amount",
    "price value",
    "document generation",
)


class FunnelRulePartitioner:
    def partition(
        self,
        rules: list[FunnelRawRule],
        *,
        source_version: str = "unknown",
    ) -> FunnelPartitionReport:
        report = FunnelPartitionReport(source_version=source_version)
        for rule in rules:
            drop_reason = self._drop_reason(rule)
            if drop_reason is not None:
                report.dropped_rules.append(DroppedFunnelRule(rule=rule, reason=drop_reason))
                continue
            if self._is_crm_rule(rule):
                report.crm_rules.append(rule)
                continue
            if self._is_user_language_rule(rule):
                report.user_language_rules.append(rule)
                continue
            report.dropped_rules.append(
                DroppedFunnelRule(rule=rule, reason="unknown_or_unapproved_section")
            )
        return report

    def to_trimatch_rule_pack(
        self,
        report: FunnelPartitionReport,
        *,
        version: str = "funnel_stage_rules.partitioned.v1",
    ) -> RulePack:
        return RulePack(
            version=version,
            rules=[
                TriMatchRule(
                    id=rule.id,
                    layer=rule.layer,
                    target=RuleTarget(funnel_stage=rule.stage),
                    phrases=rule.phrases,
                    regex=rule.regex,
                    pattern=rule.pattern,
                    confidence=rule.confidence,
                    enabled=rule.enabled,
                    shortcut_allowed=False,
                )
                for rule in report.user_language_rules
            ],
        )

    def _drop_reason(self, rule: FunnelRawRule) -> str | None:
        haystack = self._haystack(rule)
        if any(marker in haystack for marker in METADATA_MARKERS):
            return "metadata_only_rule"
        if any(term in haystack for term in FORBIDDEN_DECISION_TERMS):
            return "forbidden_decision_rule"
        if (
            rule.layer == TriMatchLayer.REGEX
            and rule.regex is not None
            and self._is_metadata_regex(rule.regex)
        ):
            return "metadata_only_regex"
        return None

    def _is_crm_rule(self, rule: FunnelRawRule) -> bool:
        return any(marker in self._haystack(rule) for marker in CRM_MARKERS)

    def _is_user_language_rule(self, rule: FunnelRawRule) -> bool:
        return any(marker in self._haystack(rule) for marker in USER_LANGUAGE_MARKERS)

    def _haystack(self, rule: FunnelRawRule) -> str:
        pieces = [rule.id, rule.section, *rule.phrases, *(rule.pattern or [])]
        if rule.regex:
            pieces.append(rule.regex)
        return " ".join(pieces).lower()

    def _is_metadata_regex(self, regex: str) -> bool:
        return bool(
            re.search(
                r"\\b(count|utm|referrer|session|duration|cookie|crm_)\\b",
                regex,
                re.IGNORECASE,
            )
        )


def load_funnel_source(path: str | Path) -> tuple[str, list[FunnelRawRule]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return "unknown", [FunnelRawRule.model_validate(item) for item in raw]
    if isinstance(raw, dict):
        version = str(raw.get("version", "unknown"))
        rules = raw.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("funnel rule source must contain a rules list")
        return version, [FunnelRawRule.model_validate(item) for item in rules]
    raise ValueError("funnel rule source must be a JSON object or list")


def verify_funnel_partition(report: FunnelPartitionReport, rule_pack: RulePack) -> list[str]:
    errors: list[str] = []
    if not report.user_language_rules:
        errors.append("no user-language funnel rules available for Tri-Match")
    crm_ids = {rule.id for rule in report.crm_rules}
    loaded_ids = {rule.id for rule in rule_pack.rules}
    leaked_crm_ids = sorted(crm_ids & loaded_ids)
    if leaked_crm_ids:
        errors.append(f"CRM rules leaked into Tri-Match rule pack: {', '.join(leaked_crm_ids)}")
    for rule in rule_pack.rules:
        if rule.target.funnel_stage is None:
            errors.append(f"{rule.id}: partitioned rule is not a funnel-stage rule")
        if rule.shortcut_allowed:
            errors.append(f"{rule.id}: partitioned funnel-stage rules must not shortcut")
    for dropped in report.dropped_rules:
        if dropped.reason == "forbidden_decision_rule":
            errors.append(f"{dropped.rule.id}: forbidden decision rule present in source")
    return errors


def partition_source(path: str | Path) -> tuple[FunnelPartitionReport, RulePack]:
    version, rules = load_funnel_source(path)
    report = FunnelRulePartitioner().partition(rules, source_version=version)
    rule_pack = FunnelRulePartitioner().to_trimatch_rule_pack(report)
    return report, rule_pack
