from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any

from .types import ChoiceQuery


ACTION_DESCRIPTIONS = {
    "left": "rotate 90 degrees left without moving",
    "right": "rotate 90 degrees right without moving",
    "forward": "move one tile forward if the tile can be entered",
    "pickup": "pick up the object in the tile directly ahead",
    "drop": "drop the carried object into the empty tile directly ahead",
    "toggle": "interact with the door or box directly ahead",
}

DIRECTION_NAMES = ("east", "south", "west", "north")


@dataclass(frozen=True)
class MiniGridState:
    env: Any
    terminal: bool = False
    success: bool = False
    reward: float = 0.0


class MiniGridGameProblem:
    """Full-state adapter for a deterministic MiniGrid episode.

    This pilot deliberately exposes the full grid to every compared agent. It
    is not the partially observable BALROG protocol and must not be reported as
    an official BALROG score.
    """

    def __init__(self, env_id: str = "MiniGrid-DoorKey-5x5-v0", *, seed: int = 0) -> None:
        import gymnasium as gym
        import minigrid

        gym.register_envs(minigrid)
        env = gym.make(env_id, render_mode="rgb_array")
        observation, _ = env.reset(seed=seed)
        env.unwrapped.step_count = 0
        self.env_id = env_id
        self.seed = seed
        self.mission = str(observation["mission"])
        self._initial_state = MiniGridState(env)

    @property
    def initial_state(self) -> MiniGridState:
        return self._initial_state

    def common_state(self) -> dict[str, Any]:
        initial = self.state_payload(self._initial_state)
        return {
            "task": "MiniGrid game control",
            "environment": self.env_id,
            "mission": self.mission,
            "coordinate_system": "x increases right; y increases down",
            "static_map": initial["grid"],
            "map_legend": initial["legend"],
            "action_semantics": ACTION_DESCRIPTIONS,
            "objective": "Choose the action most likely to complete the mission within the remaining horizon.",
        }

    @staticmethod
    def _front_cell(state: MiniGridState) -> Any:
        env = state.env.unwrapped
        x, y = (int(value) for value in env.front_pos)
        return env.grid.get(x, y)

    def _actions(self, state: MiniGridState) -> tuple[str, ...]:
        if state.terminal:
            return ()
        env = state.env.unwrapped
        cell = self._front_cell(state)
        actions = ["left", "right"]
        if cell is None or cell.can_overlap():
            actions.append("forward")
        if cell is not None and cell.can_pickup() and env.carrying is None:
            actions.append("pickup")
        if cell is None and env.carrying is not None:
            actions.append("drop")
        if cell is not None and cell.type in {"door", "box"}:
            actions.append("toggle")
        return tuple(actions)

    @staticmethod
    def _object_payload(obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        payload: dict[str, Any] = {"type": obj.type, "color": obj.color}
        if obj.type == "door":
            payload.update({"is_open": bool(obj.is_open), "is_locked": bool(obj.is_locked)})
        return payload

    def state_payload(self, state: MiniGridState) -> dict[str, Any]:
        env = state.env.unwrapped
        rows: list[list[str]] = []
        objects: list[dict[str, Any]] = []
        symbols = {"wall": "#", "door": "D", "key": "K", "goal": "G", "ball": "o", "box": "B", "lava": "~"}
        agent_pos = tuple(int(value) for value in env.agent_pos)
        arrows = (">", "v", "<", "^")
        for y in range(env.height):
            row: list[str] = []
            for x in range(env.width):
                if (x, y) == agent_pos:
                    row.append(arrows[int(env.agent_dir)])
                    continue
                obj = env.grid.get(x, y)
                if obj is None:
                    row.append(".")
                    continue
                symbol = symbols.get(obj.type, "?")
                if obj.type == "door":
                    symbol = "d" if obj.is_open else "L" if obj.is_locked else "D"
                row.append(symbol)
                objects.append({"x": x, "y": y, **(self._object_payload(obj) or {})})
            rows.append(row)
        front_pos = tuple(int(value) for value in env.front_pos)
        return {
            "mission": self.mission,
            "grid": ["".join(row) for row in rows],
            "legend": {"#": "wall", ".": "empty", "K": "key", "L": "locked door", "d": "open door", "G": "goal", "^>v<": "agent direction"},
            "agent": {
                "position": list(agent_pos),
                "direction": DIRECTION_NAMES[int(env.agent_dir)],
                "carrying": self._object_payload(env.carrying),
                "front_position": list(front_pos),
                "front_object": self._object_payload(self._front_cell(state)),
            },
            "terminal": state.terminal,
            "success": state.success,
        }

    def decision_key(self, state: MiniGridState) -> str:
        env = state.env.unwrapped
        digest = hashlib.sha256()
        digest.update(env.grid.encode().tobytes())
        digest.update(bytes(int(value) for value in env.agent_pos))
        digest.update(bytes([int(env.agent_dir), int(state.terminal), int(state.success)]))
        if env.carrying is not None:
            digest.update(str(self._object_payload(env.carrying)).encode("utf-8"))
        return digest.hexdigest()

    def make_query(self, state: MiniGridState, query_id: str) -> ChoiceQuery:
        criteria: dict[str, Any] = {}
        current = self._compact_state(state)
        for action in self._actions(state):
            child = self.apply(state, action)
            criteria[action] = {
                "meaning": ACTION_DESCRIPTIONS[action],
                "one_step_effect": self._compact_state(child),
            }
        return ChoiceQuery(
            query_id=query_id,
            state=current,
            instruction="Choose the next game action most likely to complete the mission, avoiding loops and preserving future options.",
            criteria=criteria,
        )

    def _compact_state(self, state: MiniGridState) -> dict[str, Any]:
        env = state.env.unwrapped
        doors: list[dict[str, Any]] = []
        loose_keys: list[dict[str, Any]] = []
        for y in range(env.height):
            for x in range(env.width):
                obj = env.grid.get(x, y)
                if obj is None:
                    continue
                if obj.type == "door":
                    doors.append(
                        {
                            "position": [x, y],
                            "color": obj.color,
                            "open": bool(obj.is_open),
                            "locked": bool(obj.is_locked),
                        }
                    )
                elif obj.type == "key":
                    loose_keys.append({"position": [x, y], "color": obj.color})
        return {
            "agent_position": [int(value) for value in env.agent_pos],
            "agent_direction": DIRECTION_NAMES[int(env.agent_dir)],
            "carrying": self._object_payload(env.carrying),
            "front_position": [int(value) for value in env.front_pos],
            "front_object": self._object_payload(self._front_cell(state)),
            "doors": doors,
            "loose_keys": loose_keys,
            "terminal": state.terminal,
            "success": state.success,
        }

    def apply(self, state: MiniGridState, action_key: str) -> MiniGridState:
        if action_key not in self._actions(state):
            raise ValueError(f"illegal MiniGrid action: {action_key}")
        env = copy.deepcopy(state.env)
        unwrapped = env.unwrapped
        unwrapped.step_count = 0
        action = getattr(unwrapped.actions, action_key)
        _, reward, terminated, truncated, _ = env.step(action)
        unwrapped.step_count = 0
        terminal = bool(terminated or truncated)
        success = bool(terminated and reward > 0)
        return MiniGridState(env, terminal=terminal, success=success, reward=float(reward))

    def is_terminal(self, state: MiniGridState) -> bool:
        return state.terminal

    def terminal_outcome(self, state: MiniGridState) -> dict[str, Any]:
        if not state.terminal:
            raise ValueError("terminal outcome requested for a non-terminal MiniGrid state")
        return {
            "label": "success" if state.success else "failure",
            "success": state.success,
            "terminal": True,
            "reward": state.reward,
        }
