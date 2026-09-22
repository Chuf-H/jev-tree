from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, TypeAlias

Atom: TypeAlias = tuple[str, ...]
State: TypeAlias = frozenset[Atom]


@dataclass(frozen=True, order=True)
class Action:
    name: str
    args: tuple[str, ...]

    @property
    def key(self) -> str:
        return ":".join((self.name, *self.args))

    @property
    def pddl(self) -> str:
        return f"({self.name} {' '.join(self.args)})"

    @property
    def description(self) -> str:
        match self.name, self.args:
            case "pick-up", (block,):
                return f"pick up clear block {block} from the table"
            case "put-down", (block,):
                return f"put held block {block} onto the table"
            case "stack", (block, under):
                return f"stack held block {block} on clear block {under}"
            case "unstack", (block, under):
                return f"unstack clear block {block} from block {under}"
            case _:
                return self.pddl

    @classmethod
    def parse(cls, text: str) -> "Action":
        tokens = re.findall(r"[A-Za-z0-9_-]+", text.lower())
        if not tokens:
            raise ValueError(f"not an action: {text!r}")
        name = tokens[0]
        args = tuple(tokens[1:])
        expected = {"pick-up": 1, "put-down": 1, "stack": 2, "unstack": 2}
        if name not in expected or len(args) != expected[name]:
            raise ValueError(f"invalid Blocksworld action: {text!r}")
        return cls(name, args)


@dataclass(frozen=True)
class BlocksworldProblem:
    name: str
    objects: tuple[str, ...]
    initial: State
    goal: frozenset[Atom]
    source_text: str = ""

    def is_goal(self, state: State) -> bool:
        return self.goal.issubset(state)

    def legal_actions(self, state: State) -> tuple[Action, ...]:
        clear = {atom[1] for atom in state if atom[0] == "clear"}
        ontable = {atom[1] for atom in state if atom[0] == "ontable"}
        on = {(atom[1], atom[2]) for atom in state if atom[0] == "on"}
        holdings = [atom[1] for atom in state if atom[0] == "holding"]
        actions: list[Action] = []
        if ("handempty",) in state:
            actions.extend(Action("pick-up", (block,)) for block in sorted(clear & ontable))
            actions.extend(Action("unstack", (top, under)) for top, under in sorted(on) if top in clear)
        elif len(holdings) == 1:
            held = holdings[0]
            actions.append(Action("put-down", (held,)))
            actions.extend(Action("stack", (held, under)) for under in sorted(clear) if under != held)
        else:
            raise ValueError("invalid state: expected handempty or exactly one held block")
        return tuple(actions)

    def apply(self, state: State, action: Action) -> State:
        if action not in self.legal_actions(state):
            raise ValueError(f"illegal action {action.pddl}")
        atoms = set(state)
        match action.name, action.args:
            case "pick-up", (block,):
                atoms -= {("clear", block), ("ontable", block), ("handempty",)}
                atoms.add(("holding", block))
            case "put-down", (block,):
                atoms.discard(("holding", block))
                atoms |= {("clear", block), ("handempty",), ("ontable", block)}
            case "stack", (block, under):
                atoms -= {("holding", block), ("clear", under)}
                atoms |= {("handempty",), ("clear", block), ("on", block, under)}
            case "unstack", (block, under):
                atoms -= {("on", block, under), ("clear", block), ("handempty",)}
                atoms |= {("holding", block), ("clear", under)}
            case _:
                raise AssertionError(action)
        return frozenset(atoms)

    def execute(self, actions: Iterable[Action]) -> tuple[State, int, str | None]:
        state = self.initial
        for index, action in enumerate(actions):
            try:
                state = self.apply(state, action)
            except ValueError as exc:
                return state, index, str(exc)
        return state, index + 1 if "index" in locals() else 0, None

    def goal_distance(self, state: State) -> int:
        """Goal-tower-aware ordering heuristic; never used as a correctness test.

        An upper goal relation is not stable when its support must still move. For
        example, if the goal is ``c on b on a``, ``c on b`` earns no settled credit
        until ``b on a`` is also true. This avoids a common local-atom trap.
        """
        desired_support: dict[str, str] = {}
        constrained: set[str] = set()
        for atom in self.goal:
            if atom[0] == "on":
                desired_support[atom[1]] = atom[2]
                constrained.add(atom[1])
            elif atom[0] == "ontable":
                desired_support[atom[1]] = "__table__"
                constrained.add(atom[1])
        actual_support: dict[str, str] = {}
        above: dict[str, str] = {}
        for atom in state:
            if atom[0] == "on":
                actual_support[atom[1]] = atom[2]
                above[atom[2]] = atom[1]
            elif atom[0] == "ontable":
                actual_support[atom[1]] = "__table__"
            elif atom[0] == "holding":
                actual_support[atom[1]] = "__held__"

        memo: dict[str, bool] = {}

        def settled(block: str, visiting: frozenset[str] = frozenset()) -> bool:
            if block not in desired_support:
                return True
            if block in memo:
                return memo[block]
            if block in visiting:
                memo[block] = False
                return False
            desired = desired_support[block]
            if actual_support.get(block) != desired:
                memo[block] = False
            elif desired == "__table__":
                memo[block] = True
            else:
                memo[block] = settled(desired, visiting | {block})
            return memo[block]

        unsettled = {block for block in constrained if not settled(block)}
        if not unsettled:
            return 0
        blockers: set[str] = set()

        def add_blocks_above(block: str) -> None:
            seen: set[str] = set()
            while block in above and block not in seen:
                seen.add(block)
                block = above[block]
                blockers.add(block)

        for block in unsettled:
            add_blocks_above(block)
            desired = desired_support[block]
            if desired != "__table__":
                add_blocks_above(desired)
        holding_penalty = 1 if any(atom[0] == "holding" for atom in state) else 0
        return 2 * len(unsettled) + len(blockers) + holding_penalty

    def compact_state(self, state: State) -> dict[str, object]:
        return {
            "on": sorted([list(atom[1:]) for atom in state if atom[0] == "on"]),
            "on_table": sorted(atom[1] for atom in state if atom[0] == "ontable"),
            "clear": sorted(atom[1] for atom in state if atom[0] == "clear"),
            "holding": next((atom[1] for atom in state if atom[0] == "holding"), None),
            "hand_empty": ("handempty",) in state,
        }

    def compact_goal(self) -> list[list[str]]:
        return [list(atom) for atom in sorted(self.goal)]


def _tokenize(text: str) -> list[str]:
    text = re.sub(r";[^\n]*", "", text.lower())
    return re.findall(r"\(|\)|[^\s()]+", text)


def _parse_expr(tokens: Iterator[str]) -> object:
    token = next(tokens)
    if token != "(":
        return token
    result: list[object] = []
    for token in tokens:
        if token == ")":
            return result
        if token == "(":
            result.append(_parse_expr(iter([token, *tokens])))
            return result
        result.append(token)
    raise ValueError("unbalanced PDDL")


def _parse_all(tokens: list[str]) -> object:
    index = 0

    def parse_one() -> object:
        nonlocal index
        if index >= len(tokens):
            raise ValueError("unexpected end of PDDL")
        token = tokens[index]
        index += 1
        if token != "(":
            return token
        result: list[object] = []
        while index < len(tokens) and tokens[index] != ")":
            result.append(parse_one())
        if index >= len(tokens):
            raise ValueError("unbalanced PDDL")
        index += 1
        return result

    expression = parse_one()
    if index != len(tokens):
        raise ValueError("trailing PDDL tokens")
    return expression


def _find_section(expression: object, name: str) -> list[object]:
    if isinstance(expression, list):
        if expression and expression[0] == name:
            return expression
        for child in expression:
            try:
                return _find_section(child, name)
            except KeyError:
                pass
    raise KeyError(name)


def _atom(value: object) -> Atom:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"invalid atom: {value!r}")
    return tuple(value)


def parse_problem(source: str | Path) -> BlocksworldProblem:
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    elif "(define" in source.lower():
        text = source
    else:
        candidate = Path(source)
        text = candidate.read_text(encoding="utf-8") if candidate.exists() else source
    root = _parse_all(_tokenize(text))
    problem_section = _find_section(root, "problem")
    objects_section = _find_section(root, ":objects")
    init_section = _find_section(root, ":init")
    goal_section = _find_section(root, ":goal")
    name = str(problem_section[1])
    objects = tuple(str(value) for value in objects_section[1:] if isinstance(value, str) and not value.startswith("-"))
    initial = frozenset(_atom(value) for value in init_section[1:])
    goal_expr = goal_section[1]
    if not isinstance(goal_expr, list):
        raise ValueError("invalid goal")
    goal_values = goal_expr[1:] if goal_expr and goal_expr[0] == "and" else [goal_expr]
    goal = frozenset(_atom(value) for value in goal_values)
    return BlocksworldProblem(name=name, objects=objects, initial=initial, goal=goal, source_text=text)
