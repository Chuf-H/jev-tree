from __future__ import annotations

import json
import math
from importlib.resources import files
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen

from jevtree.demo_server import DemoHandler, build_demo_result
from jevtree.providers import HeuristicBackend


def test_demo_result_is_compact_and_self_consistent() -> None:
    payload = build_demo_result(
        [1, 2, 3, 4],
        exploration_epsilon=0.0,
        backend=HeuristicBackend(),
    )

    assert payload["run_kind"] == "live"
    assert payload["metrics"]["leaves"] == 3872
    assert payload["metrics"]["mass_error"] <= 1e-9
    assert len(payload["decisions"]) == 3
    assert payload["decisions"][0]["before"] == ["1", "2", "3", "4"]
    assert payload["decisions"][-1]["after"] == ["24"]
    assert payload["recommended"]["success"] is True
    assert payload["tree_visual"]["node_count"] == 4557
    assert payload["tree_visual"]["max_depth"] == 3
    assert payload["tree_visual"]["nodes"][0]["mass"] == 1.0
    assert payload["tree_visual"]["nodes"][0]["success_mass"] > 0.0
    assert any(node["policy"] and node["terminal"] for node in payload["tree_visual"]["nodes"])
    assert len(json.dumps(payload)) < 1_000_000
    assert "api_key" not in str(payload).lower()


def test_recorded_tree_is_a_provenance_backed_probability_ledger() -> None:
    javascript = files("jevtree.web").joinpath("recorded_tree.js").read_text(
        encoding="utf-8"
    )
    prefix = "window.JEVTREE_RECORDED="
    assert javascript.startswith(prefix)
    assert javascript.endswith(";\n")
    payload = json.loads(javascript[len(prefix) : -2])
    tree = payload["tree_visual"]
    nodes = tree["nodes"]

    assert payload["run_kind"] == "recorded"
    assert payload["source_artifact_sha256"] == (
        "6cae719e7bdd824340de8d3ac6ade49e89288c0b19901a2d1a0b366a2cfe89f9"
    )
    assert tree["node_count"] == len(nodes) == 4557
    assert payload["metrics"]["leaves"] == 3872
    assert math.isclose(nodes[0]["mass"], 1.0, abs_tol=1e-12)
    assert math.isclose(
        nodes[0]["success_mass"],
        payload["outcome_probability"]["success"],
        abs_tol=1e-12,
    )

    children: list[list[int]] = [[] for _ in nodes]
    for index, node in enumerate(nodes[1:], start=1):
        assert 0 <= node["parent"] < index
        children[node["parent"]].append(index)
    for index, child_indices in enumerate(children):
        if not child_indices:
            continue
        assert math.isclose(
            nodes[index]["mass"],
            math.fsum(nodes[child]["mass"] for child in child_indices),
            abs_tol=1e-10,
        )
        assert math.isclose(
            nodes[index]["success_mass"],
            math.fsum(nodes[child]["success_mass"] for child in child_indices),
            abs_tol=1e-10,
        )
    assert [
        sum(node["policy"] and node["depth"] == depth for node in nodes)
        for depth in range(tree["max_depth"] + 1)
    ] == [1, 1, 1, 1]


def test_demo_layout_mirrors_policy_left_and_checks_api_schema() -> None:
    html = files("jevtree.web").joinpath("index.html").read_text(encoding="utf-8")

    assert "width: min(980px, 100%)" in html
    assert "1 - node.x / maxX" in html
    assert 'id="apiState"' in html
    assert 'health.capabilities?.includes("tree_visual")' in html


def test_demo_health_and_local_file_cors() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base}/api/health", timeout=2) as response:
            health = json.load(response)
        assert health["schema_version"] == 2
        assert "tree_visual" in health["capabilities"]

        request = Request(
            f"{base}/api/game24",
            method="OPTIONS",
            headers={"Origin": "null", "Access-Control-Request-Method": "POST"},
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == "null"
            assert "POST" in response.headers["Access-Control-Allow-Methods"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
