"""Track spec rules engine (Python implementation).

Evaluates the declarative rules in ``track-rules.json`` against a track spec, after validating
the spec (and optional geometry facts) against ``track-spec.schema.json``. The same semantics are
implemented in TypeScript (``src/web/src/rules``) and C# (``src/Api/Rules``); all three run the
shared vectors in ``schema/test-vectors``. See ``schema/README.md`` for the rules format.

The engine never computes geometry: rules marked ``evaluatedBy: geometry`` read measurements
from ``facts`` (produced by the geometry core) and are reported as deferred when those are absent.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

__all__ = [
    "DisabledOption",
    "EvaluationResult",
    "Flag",
    "SchemaError",
    "Violation",
    "evaluate",
    "evaluate_expr",
    "load_rules",
    "load_schema",
]


@dataclass(frozen=True)
class SchemaError:
    path: str
    message: str


@dataclass(frozen=True)
class Violation:
    rule_id: str
    severity: str
    message: str
    path: str


@dataclass(frozen=True)
class DisabledOption:
    rule_id: str
    option: str
    values: list[Any]
    hidden: bool
    reason: str


@dataclass(frozen=True)
class Flag:
    rule_id: str
    flag: str
    message: str


@dataclass
class EvaluationResult:
    schema_valid: bool
    schema_errors: list[SchemaError] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    disabled: list[DisabledOption] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.schema_valid and not any(v.severity == "error" for v in self.violations)


def _load_json(name: str) -> Any:
    text = resources.files("trackgen_geom.generated").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


@cache
def load_schema() -> dict[str, Any]:
    return _load_json("track-spec.schema.json")


@cache
def load_rules() -> dict[str, Any]:
    return _load_json("track-rules.json")


@cache
def _validators() -> tuple[Draft202012Validator, Draft202012Validator]:
    schema = load_schema()
    facts_schema = {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        "$ref": "#/$defs/GeometryFacts",
    }
    return Draft202012Validator(schema), Draft202012Validator(facts_schema)


def _pointer(parts: Sequence[Any]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _schema_errors(validator: Draft202012Validator, instance: Any, prefix: str) -> list[SchemaError]:
    errors = sorted(validator.iter_errors(instance), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    return [SchemaError(prefix + _pointer(e.absolute_path), e.message) for e in errors]


# ---------------------------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------------------------


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _truthy(v: Any) -> bool:
    return v is True


def _step(cur: Any, key: Any) -> Any:
    if isinstance(cur, Mapping):
        return cur.get(key) if isinstance(key, str) else None
    if isinstance(cur, list):
        if isinstance(key, str) and key.isdigit():
            key = int(key)
        if _is_num(key) and float(key).is_integer() and 0 <= int(key) < len(cur):
            return cur[int(key)]
    return None


def _lookup(path: str, ctx: Mapping[str, Any], scope: Mapping[str, Any]) -> Any:
    head, *rest = path.split(".")
    cur = scope.get(head) if head.startswith("$") else ctx.get(head)
    for seg in rest:
        if cur is None:
            return None
        cur = _step(cur, seg)
    return cur


def _equals(a: Any, b: Any) -> bool:
    if _is_num(a) and _is_num(b):
        return float(a) == float(b)
    if _is_num(a) or _is_num(b):
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equals(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_equals(a[k], b[k]) for k in a)
    return a == b


def _nums(vals: list[Any]) -> list[float] | None:
    return [float(v) for v in vals] if all(_is_num(v) for v in vals) else None


def evaluate_expr(expr: Any, ctx: Mapping[str, Any], scope: Mapping[str, Any] | None = None) -> Any:
    """Evaluate a rules-DSL expression. ``ctx`` holds the roots (meta, sketch, options, facts,
    constants); ``scope`` holds ``$``-prefixed bindings."""
    scope = scope or {}

    def ev(e: Any) -> Any:
        return evaluate_expr(e, ctx, scope)

    if expr is None or isinstance(expr, (bool, int, float, str)):
        return expr
    if isinstance(expr, list):
        return [ev(e) for e in expr]
    if not isinstance(expr, Mapping) or len(expr) != 1:
        raise ValueError(f"invalid expression: {expr!r}")
    op, arg = next(iter(expr.items()))

    if op == "var":
        return _lookup(arg, ctx, scope)
    if op == "get":
        return _step(ev(arg[0]), ev(arg[1]))
    if op == "exists":
        return ev(arg) is not None
    if op == "len":
        v = ev(arg)
        return len(v) if isinstance(v, (list, str, Mapping)) else 0
    if op in ("==", "!="):
        eq = _equals(ev(arg[0]), ev(arg[1]))
        return eq if op == "==" else not eq
    if op in ("<", "<=", ">", ">="):
        a, b = ev(arg[0]), ev(arg[1])
        if not (_is_num(a) and _is_num(b)):
            return False
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    if op == "in":
        needle, hay = ev(arg[0]), ev(arg[1])
        return isinstance(hay, list) and any(_equals(needle, h) for h in hay)
    if op == "and":
        return all(_truthy(ev(e)) for e in arg)
    if op == "or":
        return any(_truthy(ev(e)) for e in arg)
    if op == "not":
        return not _truthy(ev(arg))
    if op == "if":
        return ev(arg[1]) if _truthy(ev(arg[0])) else ev(arg[2])
    if op in ("+", "*"):
        nums = _nums([ev(e) for e in arg])
        if nums is None:
            return None
        return sum(nums) if op == "+" else math.prod(nums)
    if op in ("-", "/"):
        nums = _nums([ev(arg[0]), ev(arg[1])])
        if nums is None:
            return None
        if op == "-":
            return nums[0] - nums[1]
        return None if nums[1] == 0 else nums[0] / nums[1]
    if op in ("ceil", "floor"):
        v = ev(arg)
        if not _is_num(v):
            return None
        return float(math.ceil(v) if op == "ceil" else math.floor(v))
    if op in ("any", "all", "count"):
        items = ev(arg["over"])
        items = items if isinstance(items, list) else []
        name = "$" + arg["as"]
        hits = [_truthy(evaluate_expr(arg["where"], ctx, {**scope, name: item})) for item in items]
        if op == "any":
            return any(hits)
        if op == "all":
            return all(hits)
        return sum(hits)
    raise ValueError(f"unknown operator: {op}")


def format_number(x: float) -> str:
    """Formats a number for messages and option keys: take the shortest round-trip decimal
    representation, round half away from zero to 2 decimals, trim trailing zeros (and the point).
    Implemented digit-by-digit so Python, TypeScript and C# agree exactly."""
    if not math.isfinite(x):
        return "?"
    text = repr(abs(float(x))).lower() if isinstance(x, float) else str(abs(x))
    mantissa, _, exp = text.partition("e")
    int_digits, _, frac_digits = mantissa.partition(".")
    digits = int_digits + frac_digits
    point = len(int_digits) + (int(exp) if exp else 0)
    if point <= 0:
        int_part, frac = "0", "0" * -point + digits
    elif point >= len(digits):
        int_part, frac = digits + "0" * (point - len(digits)), ""
    else:
        int_part, frac = digits[:point], digits[point:]
    scaled = int(int_part + (frac + "00")[:2])
    if len(frac) > 2 and frac[2] >= "5":
        scaled += 1
    s = str(scaled).rjust(3, "0")
    whole, cents = s[:-2].lstrip("0") or "0", s[-2:].rstrip("0")
    out = f"{whole}.{cents}" if cents else whole
    return out if out == "0" or x > 0 else "-" + out


def format_value(v: Any) -> str:
    if v is None:
        return "?"
    if isinstance(v, bool):
        return "true" if v else "false"
    if _is_num(v):
        return format_number(v)
    if isinstance(v, str):
        return v
    return json.dumps(v, separators=(",", ":"))


def _render(template: str, ctx: Mapping[str, Any], scope: Mapping[str, Any]) -> str:
    out: list[str] = []
    i = 0
    while i < len(template):
        start = template.find("{", i)
        end = template.find("}", start + 1) if start >= 0 else -1
        if start < 0 or end < 0:
            out.append(template[i:])
            break
        out.append(template[i:start])
        out.append(format_value(_lookup(template[start + 1 : end], ctx, scope)))
        i = end + 1
    return "".join(out)


# ---------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------


def _to_plain(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else value


def evaluate(
    spec: Mapping[str, Any] | Any,
    facts: Mapping[str, Any] | Any | None = None,
    *,
    rules: Mapping[str, Any] | None = None,
) -> EvaluationResult:
    """Validate ``spec`` (a dict or generated ``TrackSpec``) and evaluate the rules.

    ``facts`` are optional geometry measurements (dict or generated ``GeometryFacts``).
    Rules are skipped if the spec or facts fail schema validation.
    """
    spec = _to_plain(spec)
    facts = _to_plain(facts)
    spec_validator, facts_validator = _validators()
    errors = _schema_errors(spec_validator, spec, "")
    if facts is not None:
        errors += _schema_errors(facts_validator, facts, "/facts")
    if errors:
        return EvaluationResult(schema_valid=False, schema_errors=errors)

    rules = rules if rules is not None else load_rules()
    result = EvaluationResult(schema_valid=True)
    ctx = {
        "meta": spec.get("meta"),
        "sketch": spec.get("sketch"),
        "options": spec.get("options"),
        "facts": facts if facts is not None else {},
        "constants": rules.get("constants", {}),
    }
    for rule in rules["rules"]:
        _evaluate_rule(rule, ctx, result)
    return result


def _evaluate_rule(rule: Mapping[str, Any], ctx: Mapping[str, Any], result: EvaluationResult) -> None:
    rule_id = rule["id"]
    if any(_lookup(p, ctx, {}) is None for p in rule.get("requires", [])):
        result.deferred.append(rule_id)
        return

    for_each = rule.get("forEach")
    if for_each:
        items = _lookup(for_each["path"], ctx, {})
        items = items if isinstance(items, list) else []
        base = "/" + for_each["path"].replace(".", "/")
        frames = [({"$" + for_each["as"]: item, "$index": i}, f"{base}/{i}") for i, item in enumerate(items)]
    else:
        frames = [({}, "")]

    effect = rule["effect"]
    kind = effect["type"]
    for scope, item_path in frames:
        for name, expr in rule.get("let", {}).items():
            scope["$" + name] = evaluate_expr(expr, ctx, scope)
        if "when" in rule and not _truthy(evaluate_expr(rule["when"], ctx, scope)):
            continue
        if any(_lookup(p, ctx, scope) is None for p in rule.get("requiresEach", [])):
            if rule_id not in result.deferred:
                result.deferred.append(rule_id)
            continue
        path = effect.get("path", item_path)

        if kind in ("disable", "hide"):
            option, values = effect["option"], list(effect["values"])
            result.disabled.append(DisabledOption(rule_id, option, values, kind == "hide", effect["reason"]))
            current = (ctx["options"] or {}).get(option)
            selected = current if isinstance(current, list) else [current]
            if any(_equals(s, v) for s in selected for v in values):
                result.violations.append(Violation(rule_id, "error", effect["reason"], f"/options/{option}"))
        elif kind == "require":
            if not _truthy(evaluate_expr(effect["assert"], ctx, scope)):
                message = _render(effect["message"], ctx, scope)
                result.violations.append(Violation(rule_id, effect["severity"], message, path))
        elif kind in ("error", "warning"):
            result.violations.append(Violation(rule_id, kind, _render(effect["message"], ctx, scope), path))
        elif kind == "flag":
            result.flags.append(Flag(rule_id, effect["flag"], _render(effect["message"], ctx, scope)))
        else:
            raise ValueError(f"rule {rule_id}: unknown effect type {kind}")
