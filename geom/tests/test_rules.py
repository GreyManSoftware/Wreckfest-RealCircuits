"""Rules engine tests: shared vectors (schema/test-vectors) plus unit tests for the expression DSL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from trackgen_geom import rules
from trackgen_geom.generated import GeometryFacts, Options, TrackSpec

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"
VECTOR_DIR = SCHEMA_DIR / "test-vectors"
VECTORS = sorted(VECTOR_DIR.glob("*.json"))


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def summarise(result: rules.EvaluationResult) -> dict[str, Any]:
    def ids(severity: str) -> list[str]:
        return sorted({v.rule_id for v in result.violations if v.severity == severity})

    def options(hidden: bool) -> list[str]:
        return sorted(
            {f"{d.option}={rules.format_value(v)}" for d in result.disabled if d.hidden == hidden for v in d.values}
        )

    return {
        "schemaValid": result.schema_valid,
        "valid": result.valid,
        "errors": ids("error"),
        "warnings": ids("warning"),
        "disabled": options(False),
        "hidden": options(True),
        "flags": sorted({f.flag for f in result.flags}),
        "deferred": sorted(result.deferred),
    }


def test_vectors_exist() -> None:
    assert len(VECTORS) >= 15


@pytest.mark.parametrize("path", VECTORS, ids=[p.stem for p in VECTORS])
def test_vector(path: Path) -> None:
    vector = load(path)
    expect = dict(vector["expect"])
    expected_paths = expect.pop("schemaErrorPaths", [])

    result = rules.evaluate(vector["spec"], vector.get("facts"))

    assert summarise(result) == expect, [e.message for e in result.schema_errors]
    actual_paths = {e.path for e in result.schema_errors}
    for p in expected_paths:
        assert p in actual_paths, f"expected a schema error at {p!r}, got {sorted(actual_paths)}"


@pytest.mark.parametrize("path", VECTORS, ids=[p.stem for p in VECTORS])
def test_vector_round_trips_through_generated_types(path: Path) -> None:
    vector = load(path)
    if not vector["expect"]["schemaValid"]:
        pytest.skip("schema-invalid vector")
    spec = TrackSpec.from_dict(vector["spec"])
    assert spec.to_dict() == vector["spec"]
    facts = GeometryFacts.from_dict(vector.get("facts", {}))
    assert facts.to_dict() == vector.get("facts", {})
    # Typed objects are accepted directly.
    assert summarise(rules.evaluate(spec, facts if "facts" in vector else None))["valid"] == vector["expect"]["valid"]


def test_every_rule_is_covered_by_a_vector() -> None:
    triggered: set[str] = set()
    for path in VECTORS:
        vector = load(path)
        result = rules.evaluate(vector["spec"], vector.get("facts"))
        triggered.update(v.rule_id for v in result.violations)
        triggered.update(d.rule_id for d in result.disabled)
        triggered.update(f.rule_id for f in result.flags)
    all_ids = {r["id"] for r in rules.load_rules()["rules"]}
    assert all_ids - triggered == set()


def test_rules_file_matches_its_schema() -> None:
    schema = load(SCHEMA_DIR / "track-rules.schema.json")
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(load(SCHEMA_DIR / "track-rules.json")))
    assert errors == []


def test_rule_ids_are_unique() -> None:
    ids = [r["id"] for r in rules.load_rules()["rules"]]
    assert len(ids) == len(set(ids))


def test_packaged_copies_match_schema_dir() -> None:
    assert rules.load_schema() == load(SCHEMA_DIR / "track-spec.schema.json")
    assert rules.load_rules() == load(SCHEMA_DIR / "track-rules.json")


def test_option_defaults_match_schema() -> None:
    schema = rules.load_schema()
    props = schema["$defs"]["Options"]["properties"]
    defaults = Options.defaults().to_dict()
    assert defaults == {k: v.get("default") for k, v in props.items()}


def test_violation_messages_are_rendered() -> None:
    vector = load(VECTOR_DIR / "grid-max-short-sector.json")
    result = rules.evaluate(vector["spec"], vector["facts"])
    [v] = result.violations
    assert v.message == "A grid of 24 needs a first sector of at least 96 m; it is 95.9 m."
    assert v.path == "/options/gridSize"


def test_for_each_violation_path_points_at_item() -> None:
    vector = load(VECTOR_DIR / "jump-on-unknown-route.json")
    [v] = rules.evaluate(vector["spec"]).violations
    assert v.path == "/sketch/jumps/0"
    assert v.message == "Jump j1 is on route alt9, which doesn't exist."


CTX = {"options": {"gridSize": 9, "weathers": ["noon"], "flag": True}, "facts": {"m": {"a": {"x": 3}}}}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ({"var": "options.gridSize"}, 9),
        ({"var": "options.missing.deep"}, None),
        ({"var": "options.weathers.0"}, "noon"),
        ({"get": [{"var": "facts.m"}, "a"]}, {"x": 3}),
        ({"exists": {"var": "options.nope"}}, False),
        ({"len": {"var": "options.weathers"}}, 1),
        ({"len": {"var": "options.nope"}}, 0),
        ({"==": [{"var": "options.gridSize"}, 9.0]}, True),
        ({"==": [1, True]}, False),
        ({"!=": ["a", "b"]}, True),
        ({"<": [None, 1]}, False),
        ({">=": [2, 2]}, True),
        ({"in": ["noon", {"var": "options.weathers"}]}, True),
        ({"in": ["noon", None]}, False),
        ({"and": [True, {"var": "options.flag"}]}, True),
        ({"and": [True, 1]}, False),
        ({"or": [False, None]}, False),
        ({"not": None}, True),
        ({"if": [True, "a", "b"]}, "a"),
        ({"+": [1, 2, 3]}, 6),
        ({"*": [2, None]}, None),
        ({"-": [5, 7]}, -2),
        ({"/": [1, 0]}, None),
        ({"ceil": {"/": [{"var": "options.gridSize"}, 2]}}, 5),
        ({"floor": 2.7}, 2),
        ({"any": {"over": [1, 2, 3], "as": "n", "where": {">": [{"var": "$n"}, 2]}}}, True),
        ({"all": {"over": [], "as": "n", "where": False}}, True),
        ({"count": {"over": [1, 2, 3], "as": "n", "where": {"<": [{"var": "$n"}, 3]}}}, 2),
    ],
)
def test_expressions(expr: Any, expected: Any) -> None:
    assert rules.evaluate_expr(expr, CTX) == expected


def test_format_value() -> None:
    assert [rules.format_value(v) for v in (None, True, 96.0, 95.9, 1 / 3, "x")] == ["?", "true", "96", "95.9", "0.33", "x"]
