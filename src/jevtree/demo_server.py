from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .game24 import Game24Problem
from .probability_tree import (
    derive_policy_path,
    enumerate_probability_tree,
    leaf_for_actions,
    pareto_next_actions,
    summarize_next_actions,
)
from .providers import TypeSafeBackend
from .types import DecisionBackend


def _load_local_env(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding the process environment."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def _values(state_payload: Any) -> list[str]:
    return [str(item["value"]) for item in state_payload]


def build_tree_visual(
    leaves: Sequence[Any],
    *,
    root_label: str,
    policy: Sequence[str],
) -> dict[str, Any]:
    """Aggregate canonical leaves into a compact prefix tree for the browser."""

    nodes: list[dict[str, Any]] = [
        {
            "parent": -1,
            "depth": 0,
            "action": root_label,
            "mass": 0.0,
            "success_mass": 0.0,
            "terminal": False,
            "success": False,
            "policy": True,
        }
    ]
    prefix_to_index: dict[tuple[str, ...], int] = {(): 0}
    max_depth = 0
    for leaf in leaves:
        actions = tuple(leaf.actions)
        probability = float(leaf.probability)
        outcome = leaf.outcome
        successful = bool(outcome.get("success"))
        nodes[0]["mass"] += probability
        if successful:
            nodes[0]["success_mass"] += probability
        prefix: tuple[str, ...] = ()
        parent = 0
        for depth, action in enumerate(actions, start=1):
            prefix = (*prefix, action)
            index = prefix_to_index.get(prefix)
            if index is None:
                index = len(nodes)
                prefix_to_index[prefix] = index
                nodes.append(
                    {
                        "parent": parent,
                        "depth": depth,
                        "action": action,
                        "mass": 0.0,
                        "success_mass": 0.0,
                        "terminal": False,
                        "success": False,
                        "policy": tuple(policy[:depth]) == prefix,
                    }
                )
            node = nodes[index]
            node["mass"] += probability
            if successful:
                node["success_mass"] += probability
            parent = index
            max_depth = max(max_depth, depth)
        node["terminal"] = True
        node["success"] = successful

    for node in nodes:
        node["mass"] = round(float(node["mass"]), 12)
        node["success_mass"] = round(float(node["success_mass"]), 12)
    return {
        "root_label": root_label,
        "policy": list(policy),
        "node_count": len(nodes),
        "max_depth": max_depth,
        "nodes": nodes,
    }


def build_demo_result(
    numbers: list[int],
    *,
    exploration_epsilon: float,
    backend: DecisionBackend,
    question_batch_size: int = 128,
) -> dict[str, Any]:
    """Run the real harness and return only the compact, browser-safe trace."""

    problem = Game24Problem(numbers)
    result = enumerate_probability_tree(
        problem,
        backend,
        max_depth=len(numbers) - 1,
        question_batch_size=question_batch_size,
        exploration_epsilon=exploration_epsilon,
        include_edges=False,
    )
    policy = derive_policy_path(result.leaves, mode="downstream_success")
    greedy = derive_policy_path(result.leaves, mode="local_greedy")
    policy_leaf = leaf_for_actions(result.leaves, policy)
    greedy_leaf = leaf_for_actions(result.leaves, greedy)

    state = problem.initial_state
    prefix: tuple[str, ...] = ()
    decisions: list[dict[str, Any]] = []
    for index, action in enumerate(policy, start=1):
        summaries = summarize_next_actions(result.leaves, prefix=prefix)
        selected = next(item for item in summaries if item.action == action)
        after = problem.apply(state, action)
        decisions.append(
            {
                "step": index,
                "before": _values(problem.state_payload(state)),
                "action": action,
                "after": _values(problem.state_payload(after)),
                "local_probability": selected.conditional_model_probability,
                "downstream_success": selected.outcome_probability.get("success", 0.0),
                "joint_probability": selected.joint_probability,
                "pareto_actions": list(pareto_next_actions(summaries)),
            }
        )
        prefix = (*prefix, action)
        state = after

    return {
        "schema_version": 1,
        "run_kind": "live",
        "task": {"name": "Game of 24", "numbers": numbers, "target": 24},
        "model": result.model,
        "probability_semantics": result.metadata["probability_semantics"],
        "exploration_epsilon": exploration_epsilon,
        "metrics": {
            "leaves": len(result.leaves),
            "logical_decision_paths": result.metadata["logical_decision_paths"],
            "unique_queried_states": result.metadata["queried_decision_states"],
            "query_compression_ratio": result.metadata["query_compression_ratio"],
            "provider_requests": result.usage.requests,
            "provider_latency_ms": result.usage.latency_ms,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "mass_error": result.mass_error,
            "raw_zero_action_probabilities": result.metadata["raw_zero_action_probabilities"],
        },
        "outcome_probability": result.outcome_probability,
        "layers": [asdict(layer) for layer in result.layers],
        "root_actions": [
            {
                **asdict(action),
                "pareto": action.action in result.root_pareto_actions,
                "success_probability": action.outcome_probability.get("success", 0.0),
            }
            for action in result.root_actions
        ],
        "recommended": {
            "actions": list(policy),
            "success": bool(policy_leaf.outcome.get("success")),
            "expression": policy_leaf.outcome.get("expression"),
            "leaf_probability": policy_leaf.probability,
        },
        "local_greedy": {
            "actions": list(greedy),
            "success": bool(greedy_leaf.outcome.get("success")),
            "expression": greedy_leaf.outcome.get("expression"),
            "leaf_probability": greedy_leaf.probability,
        },
        "tree_visual": build_tree_visual(
            result.leaves,
            root_label=" · ".join(str(number) for number in numbers),
            policy=policy,
        ),
        "decisions": decisions,
        "caution": "成功质量是该枚举树上的 Jev 路径概率质量，不是校准后的真实成功率。",
    }


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "JevTreeDemo/0.1"

    def _set_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        configured = {
            item.strip()
            for item in os.environ.get("JEVTREE_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        }
        allowed = {"null", "http://127.0.0.1:8765", "http://localhost:8765", *configured}
        if origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if urlsplit(self.path).path not in {"/api/health", "/api/game24"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._set_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            body = files("jevtree.web").joinpath("index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "api_key_configured": bool(os.environ.get("TYPESAFE_API_KEY", "").strip()),
                    "schema_version": 2,
                    "capabilities": ["tree_visual", "live_game24"],
                }
            )
            return
        if path == "/recorded_tree.js":
            body = files("jevtree.web").joinpath("recorded_tree.js").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/game24":
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 16_384:
                raise ValueError("请求体大小不合法")
            request = json.loads(self.rfile.read(size))
            numbers = [int(value) for value in request.get("numbers", [])]
            if len(numbers) != 4 or any(abs(value) > 99 for value in numbers):
                raise ValueError("请输入 4 个绝对值不超过 99 的整数")
            epsilon = float(request.get("exploration_epsilon", 0.0))
            if epsilon not in {0.0, 0.01}:
                raise ValueError("当前演示只支持 raw Jev 或 1% 均匀探索")
            backend = TypeSafeBackend(model=str(request.get("model", "jev-latest")))
            try:
                payload = build_demo_result(
                    numbers,
                    exploration_epsilon=epsilon,
                    backend=backend,
                )
            finally:
                backend.close()
            self._json(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # local demo: surface a useful but credential-free failure
            message = str(exc)
            secret = os.environ.get("TYPESAFE_API_KEY", "").strip()
            if secret:
                message = message.replace(secret, "[redacted]")
            self._json({"error": message or "Jev 请求失败"}, HTTPStatus.BAD_GATEWAY)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[jev-tree-demo] {self.address_string()} - {format % args}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local JevTree recording demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_local_env(args.env_file)
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"JevTree demo: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
