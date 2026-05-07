from __future__ import annotations

from decimal import Decimal

from .config import DependencyGraph
from .models import DurationRange, ProjectTimeline, QuoteLineItem, ServiceCategory, TimelineItem


def build_project_timeline(
    line_items: list[QuoteLineItem], dependency_graph: DependencyGraph
) -> ProjectTimeline:
    item_by_service = {ServiceCategory(item.service): item for item in line_items}
    scheduled: dict[ServiceCategory, TimelineItem] = {}
    warnings: list[str] = []

    def schedule_service(service: ServiceCategory) -> TimelineItem:
        if service in scheduled:
            return scheduled[service]
        line = item_by_service[service]
        rule = dependency_graph.dependencies.get(service)
        deps = [dep for dep in (rule.after if rule else []) if dep in item_by_service]
        dep_items = [schedule_service(dep) for dep in deps]
        if not dep_items:
            start = Decimal("0")
        else:
            start = max(dep.end_offset_day for dep in dep_items)
        if rule:
            overlap = [dep for dep in rule.can_overlap_with if dep in item_by_service]
            if overlap:
                warnings.append(
                    f"{service.value} may overlap with {', '.join(dep.value for dep in overlap)} if assets are ready."
                )
        end = start + line.final_duration_days
        scheduled[service] = TimelineItem(
            service=service,
            start_offset_day=start,
            end_offset_day=end,
            dependencies=deps,
            can_overlap=bool(rule and rule.can_overlap_with),
        )
        return scheduled[service]

    for item in line_items:
        schedule_service(ServiceCategory(item.service))

    ordered = sorted(scheduled.values(), key=lambda x: (x.start_offset_day, x.end_offset_day))
    total_high = max((item.end_offset_day for item in ordered), default=Decimal("0"))
    total_low = max(Decimal("0"), total_high * Decimal("0.9"))
    return ProjectTimeline(
        total_timeline=DurationRange(low=total_low, high=total_high, unit="business_days"),
        schedule=ordered,
        warnings=warnings,
    )
