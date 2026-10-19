import copy
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from trackgen_geom import service  # noqa: E402
from trackgen_geom.service import app  # noqa: E402

TESTS = Path(__file__).parent
SAMPLES = sorted((TESTS / "samples").glob("*.json"))
CONTRACT = TESTS / "contract"
# Specs whose /validate responses are the shared contract fixtures (read by tests/Api.Tests too): a sample
# name (tests/samples/<name>.json) or tests/contract/<name>.spec.json.
CONTRACT_CASES = ["figure8_bridge", "loop_alt_jump", "rule_failures"]
FACT_KEYS = {"crossings", "altRoutes", "unannotatedCrossings", "unmatchedCrossingAnnotations"}
RULE_KEYS = ["schemaValid", "schemaErrors", "violations", "disabled", "flags", "deferred", "valid"]


def load(name: str) -> Any:
    path = TESTS / "samples" / f"{name}.json"
    if not path.exists():
        path = CONTRACT / f"{name}.spec.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_validate_sample(client: TestClient, path: Path) -> None:
    response = client.post("/validate", json={"spec": load(path.stem)})
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["version", "valid", "facts", "ruleResult", "warnings", "errors"]
    assert body["valid"] is True
    assert FACT_KEYS <= set(body["facts"])
    assert {"firstSectorLengthM", "maxAbsCoordinateM", "minCornerRadiusM"} <= set(body["facts"])
    assert list(body["ruleResult"]) == RULE_KEYS
    assert body["ruleResult"]["schemaValid"] is True
    assert body["ruleResult"]["deferred"] == []
    assert body["errors"] == []


def test_fact_keys_present_when_empty(client: TestClient) -> None:
    facts = client.post("/validate", json={"spec": load("oval")}).json()["facts"]
    assert facts["crossings"] == {}
    assert facts["altRoutes"] == {}
    assert facts["unannotatedCrossings"] == []
    assert facts["unmatchedCrossingAnnotations"] == []


def test_rule_violations_are_still_200(client: TestClient) -> None:
    """Geom reports measurements; failing rules don't change the status code."""
    spec = load("figure8_bridge")
    stray = copy.deepcopy(spec["sketch"]["crossings"][0])
    stray.update(id="nowhere", position={"x": 20, "y": 20})
    spec["sketch"]["crossings"].append(stray)
    spec["options"].update(environment="sandpit", surface="tarmac")
    response = client.post("/validate", json={"spec": spec})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["facts"]["unmatchedCrossingAnnotations"] == ["nowhere"]
    assert any(d["ruleId"] == "sandpit-no-tarmac" for d in body["ruleResult"]["disabled"])
    assert any(v["severity"] == "error" for v in body["ruleResult"]["violations"])
    violation = next(v for v in body["ruleResult"]["violations"] if v["ruleId"] == "crossing-annotation-matches")
    assert set(violation) == {"ruleId", "severity", "message", "path"}
    assert [w["code"] for w in body["warnings"]] == ["crossing-annotation-unmatched"]


def test_preview(client: TestClient) -> None:
    response = client.post("/preview", json={"spec": load("figure8_bridge")})
    assert response.status_code == 200
    body = response.json()
    assert body["svg"].startswith("<svg")
    assert body["elevationSvg"].startswith("<svg")
    assert FACT_KEYS <= set(body["facts"])
    summary = body["modelSummary"]
    assert summary["lapLengthM"] > 0
    assert {"minX", "maxX", "minY", "maxY", "maxAbsCoordinateM"} <= set(summary["bounds"])
    assert summary["routes"][0]["id"] == "main"
    assert "model" not in body


def test_model(client: TestClient) -> None:
    body = client.post("/model", json={"spec": load("loop_alt_jump")}).json()
    assert body["model"]["formatVersion"] == 1
    assert body["model"]["routes"]
    assert FACT_KEYS <= set(body["facts"])
    t = body["terrain"]
    assert len(t["z"]) == (t["nx"] + 1) * (t["ny"] + 1)
    assert "scenery" not in body  # only on /scenery (the planner is slow)


@pytest.mark.parametrize("name", ["hilly_circuit", "loop_alt_jump"])
def test_scenery_is_the_builders_plan_in_model_coordinates(client: TestClient, name: str) -> None:
    pytest.importorskip("wfbuild")
    import numpy as np

    spec = load(name)
    response = client.post("/scenery", json={"spec": spec})
    assert response.status_code == 200
    sc = response.json()["scenery"]
    assert sc["format"] == "trackgen-scenery/1"
    assert sc["slug"] == spec["meta"]["slug"]
    assert sc["environment"] == spec["options"]["environment"]
    assert sc["transform"]["rotateDeg"] == 0 and sc["transform"]["shift"] == [0, 0, 0]
    assert sc["total"] == len(sc["placements"]) == sum(sc["counts"].values()) > 100
    assert {"trees", "spectators", "grandstand", "gantry"} <= set(sc["counts"])
    assert set(sc["assets"]) == {p["asset"] for p in sc["placements"]}
    assert not [w for w in sc["warnings"] if w.startswith("ground-fallback")]
    # Alignment with the road the 3D view draws from /model (the same coordinates): everything but the start
    # gantry keeps the planner's clearance from every road edge and stands on the terrain.
    model = client.post("/model", json={"spec": spec}).json()["model"]
    road = np.array([(q["x"], q["y"], q["width"] / 2) for r in model["routes"] for q in r["samples"]])
    b = model["bounds"]
    m = b["terrainMarginM"]
    for p in sc["placements"]:
        x, y, z = p["position"]
        assert b["minX"] - m <= x <= b["maxX"] + m and b["minY"] - m <= y <= b["maxY"] + m
        assert b["minZ"] - 30 < z < b["maxZ"] + 30
        if p["category"] != "gantry":
            clear = float(np.min(np.hypot(road[:, 0] - x, road[:, 1] - y) - road[:, 2]))
            assert clear >= sc["info"]["edgeMinM"] - 1.5, (p, clear)
    gantry = next(p for p in sc["placements"] if p["category"] == "gantry")
    finish = next(c for c in model["checkpoints"] if c["isFinish"])
    assert math.dist(gantry["position"][:2], finish["position"][:2]) < 30


@pytest.mark.parametrize("endpoint", ["/validate", "/preview", "/model", "/scenery"])
def test_deterministic(client: TestClient, endpoint: str) -> None:
    spec = load("figure8_bridge")
    first = client.post(endpoint, json={"spec": spec})
    second = client.post(endpoint, json={"spec": copy.deepcopy(spec)})
    assert first.status_code == 200
    assert first.content == second.content


def test_schema_invalid_is_422(client: TestClient) -> None:
    spec = load("oval")
    del spec["meta"]["name"]
    response = client.post("/validate", json={"spec": spec})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "schema-invalid"
    assert detail["schemaErrors"]
    assert set(detail["schemaErrors"][0]) == {"path", "message"}


def test_oversized_sketch_array_is_422(client: TestClient) -> None:
    spec = load("oval")
    spec["sketch"]["mainLoop"]["points"] = [{"x": 1.0, "y": 1.0}] * 10_001
    detail = client.post("/validate", json={"spec": spec}).json()["detail"]
    assert detail["code"] == "schema-invalid"
    assert detail["schemaErrors"][0]["path"] == "/sketch/mainLoop/points"


def test_rejected_sketch_is_422(client: TestClient) -> None:
    spec = load("oval")
    spec["sketch"]["mainLoop"]["points"] = [{"x": 500.0, "y": 300.0}] * 3
    response = client.post("/preview", json={"spec": spec})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "geometry-rejected"
    assert detail["errors"][0]["code"].startswith("main-loop-")


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"not json", 400), (b"[]", 400), (b'{"specs": {}}', 400), (b'{"spec": {"x": NaN}}', 400), (b"", 400)],
)
def test_bad_bodies(client: TestClient, content: bytes, status: int) -> None:
    response = client.post("/validate", content=content, headers={"content-type": "application/json"})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == "bad-request"


def test_oversized_body_is_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.settings, "max_body_bytes", 1000)
    response = client.post("/validate", json={"spec": load("oval")})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "body-too-large"


def test_oversized_chunked_body_is_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.settings, "max_body_bytes", 1000)

    def chunks() -> Any:
        for _ in range(10):
            yield b" " * 500

    response = client.post("/validate", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413


@pytest.mark.parametrize(("endpoint", "slow_step"), [("/validate", "build_track_model"), ("/scenery", "plan_scenery")])
def test_timeout_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch, endpoint: str, slow_step: str) -> None:
    real = getattr(service, slow_step)

    def slow(*args: Any) -> Any:
        time.sleep(0.5)
        return real(*args)

    monkeypatch.setattr(service, slow_step, slow)
    monkeypatch.setattr(service.settings, "compute_timeout_s", 0.2)
    response = client.post(endpoint, json={"spec": load("oval")})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "compute-timeout"


def test_scenery_without_the_planner_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: Any) -> Any:
        raise service.SceneryUnavailable("No module named 'wfbuild'")

    monkeypatch.setattr(service, "plan_scenery", missing)
    response = client.post("/scenery", json={"spec": load("oval")})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "scenery-unavailable"


def test_unexpected_error_is_generic_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(spec: Any) -> Any:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(service, "build_track_model", boom)
    response = client.post("/validate", json={"spec": load("oval")})
    assert response.status_code == 500
    assert response.json() == {
        "detail": {"code": "internal-error", "message": "Internal error in the geometry service."}
    }


# --- shared contract fixtures -----------------------------------------------------------------


def _close(actual: Any, expected: Any, path: str = "") -> None:
    """Same structure and types; numbers within 0.01 (the fixture is generated on one platform)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and list(actual) == list(expected), path
        for k in expected:
            _close(actual[k], expected[k], f"{path}/{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            _close(a, e, f"{path}/{i}")
    elif isinstance(expected, float) or (isinstance(expected, int) and not isinstance(expected, bool)):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), path
        assert abs(actual - expected) <= 0.01, path
    elif path == "/version":
        assert isinstance(actual, str)
    else:
        assert actual == expected, path


@pytest.mark.parametrize("name", CONTRACT_CASES)
def test_contract_fixture_matches_service(client: TestClient, name: str) -> None:
    """``tests/contract/validate-<sample>.json`` is a real /validate response; the API's tests
    deserialise it (tests/Api.Tests/GeometryContractTests.cs). Regenerate with GEOM_UPDATE_CONTRACT=1."""
    body = client.post("/validate", json={"spec": load(name)}).json()
    fixture = CONTRACT / f"validate-{name}.json"
    if os.environ.get("GEOM_UPDATE_CONTRACT") == "1":
        CONTRACT.mkdir(exist_ok=True)
        fixture.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _close(body, json.loads(fixture.read_text(encoding="utf-8")))
