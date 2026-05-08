from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError


class StrictTemplateRenderer:
    def __init__(self) -> None:
        self.environment = Environment(
            autoescape=True,
            undefined=StrictUndefined,
            trim_blocks=False,
            lstrip_blocks=False,
        )

    def render(self, template_path: Path, params: dict[str, Any]) -> str:
        source = template_path.read_text(encoding="utf-8")
        jinja_source = self._convert_ejs_to_jinja(source)
        try:
            template = self.environment.from_string(jinja_source)
        except TemplateSyntaxError as exc:
            raise ValueError(f"template conversion failed: {exc}") from exc
        return template.render(**params)

    def _convert_ejs_to_jinja(self, source: str) -> str:
        converted = re.sub(
            r"<%=\s*(.*?)\s*%>",
            lambda match: self._expr(match.group(1)),
            source,
            flags=re.DOTALL,
        )
        converted = re.sub(
            r"<%\s*(.*?)\s*%>",
            lambda match: self._control(match.group(1)),
            converted,
            flags=re.DOTALL,
        )
        if "<%" in converted or "%>" in converted:
            raise ValueError("unsupported EJS tag remained after conversion")
        return converted

    def _expr(self, expression: str) -> str:
        expr = expression.strip()
        expr = expr.replace(
            "beforeOrAfter ? 'before' : 'after'",
            "'before' if beforeOrAfter else 'after'",
        )
        expr = re.sub(r"`1\.2\.\$\{index \+ 1\}`", '"1.2." ~ loop.index', expr)
        return "{{ " + expr + " }}"

    def _control(self, code: str) -> str:
        normalized = " ".join(code.strip().split()).rstrip(";")
        foreach = re.fullmatch(
            r"([A-Za-z0-9_.]+)\.forEach\(\(?([A-Za-z_][A-Za-z0-9_]*)(?:,\s*index)?\)?\s*=>\s*\{",
            normalized,
        )
        if foreach:
            collection, variable = foreach.groups()
            collection = self._collection(collection)
            return "{% for " + variable + " in " + collection + " %}"
        foreach_fn = re.fullmatch(
            r"([A-Za-z0-9_.]+)\.forEach\(function\(([A-Za-z_][A-Za-z0-9_]*)\) \{",
            normalized,
        )
        if foreach_fn:
            collection, variable = foreach_fn.groups()
            collection = self._collection(collection)
            return "{% for " + variable + " in " + collection + " %}"
        if normalized in {"})", "});"}:
            return "{% endfor %}"
        if normalized == "}":
            return "{% endif %}"
        if normalized.startswith("if ") or normalized.startswith("if("):
            condition = normalized.removeprefix("if").strip()
            condition = self._strip_js_condition(condition)
            return "{% if " + self._condition(condition) + " %}"
        if normalized.startswith("} else if"):
            condition = normalized.removeprefix("} else if").strip()
            condition = self._strip_js_condition(condition)
            return "{% elif " + self._condition(condition) + " %}"
        if normalized.startswith("else if"):
            condition = normalized.removeprefix("else if").strip()
            condition = self._strip_js_condition(condition)
            return "{% elif " + self._condition(condition) + " %}"
        if normalized in {"} else {", "else {"}:
            return "{% else %}"
        raise ValueError(f"unsupported EJS control tag: {code}")

    def _condition(self, condition: str) -> str:
        return condition.replace("&&", " and ").replace("||", " or ").replace("===", "==")

    def _strip_js_condition(self, condition: str) -> str:
        stripped = condition.strip()
        if stripped.endswith("{"):
            stripped = stripped[:-1].strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            stripped = stripped[1:-1].strip()
        return stripped

    def _collection(self, collection: str) -> str:
        if collection.endswith(".items"):
            return collection.removesuffix(".items") + '["items"]'
        return collection
