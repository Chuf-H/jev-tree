# AGENTS.md — Guidelines for AI Coding Agents

## 1. Project Overview & Architectural Intent
`jev-tree` is a typed Python runtime for finite multi-step decision problems. It turns fast local probability judgments into an executable probability graph, aggregating path mass and selecting actions via downstream outcome quality instead of one-step greedy heuristics.

- **Primary Package:** `src/jevtree/` (CLI: `jevtree.cli`, Server: `jevtree.demo_server`)
- **EVM Capability Verifier:** `guard7702/` (Foundry test suite, TypeScript reachability engine, and interactive visualizer for EIP-7702 / Permit2)
- **Key Invariants:** Exact probability mass conservation ($|\sum P - 1.0| < 10^{-14}$), typed state transitions, explicit terminal verification, and deterministic safety gates.
- **Reference Docs (Progressive Disclosure):**
  - `README.md`: Runtime architecture, probability mass semantics, and benchmarks.
  - `docs/DEEP_RESEARCH_REPORT.md`: Comprehensive academic gap research, Post-Pectra threat model & 42 verified IEEE citations.
  - `docs/papers/`: Full-text corpora of foundational 2025–2026 academic research papers.

---

## 2. Mandatory Metacognitive Oversight: Superego (`sg`)
**All AI coding agents must actively utilize Superego (`sg`) throughout execution, regardless of model capability or speed.** High-velocity models (such as Gemini 3.8 Flash) are especially prone to premature termination, shallow verification, and silent scope creep. Superego enforces supervisory meta-cognitive guardrails.

### Operational Triggers:
1. **At Task Initialization & Mode Check:**
   - Ensure the Superego coding prompt is active:
     ```bash
     command -v sg >/dev/null && sg prompt switch code
     ```
2. **At Critical Decision Points & Pre-Implementation:**
   - When evaluating competing architectural trade-offs or framing complex adapters, inspect the decision audit trail:
     ```bash
     command -v sg >/dev/null && sg history
     ```
3. **Pre-Commit & Verification Gate (MANDATORY):**
   - **NEVER** mark a task as complete, commit changes, or push to git without running:
     ```bash
     command -v sg >/dev/null && sg review
     ```
   - For Antigravity LLM-specific auditing:
     ```bash
     command -v sg >/dev/null && sg review-agy
     ```
4. **Handling Superego Feedback:**
   - **Critically Reason:** Inspect flagged concerns (scope creep, unhandled edge cases, missing error boundaries, or false assumptions).
   - If valid, refine code and tests immediately. If a false positive occurs on grounded technical grounds, explicitly justify the trade-off.

---

## 3. Development, Build & Verification Commands
- **Environment:** Python >= 3.10, `hatchling` build backend.
- **Run Unit Tests:**
  ```bash
  pytest -q
  ```
- **Run Tests with Coverage (requires `pip install -e .[dev]`):**
  ```bash
  pytest --cov=jevtree -q
  ```
- **Syntax & Bytecode Compilation:**
  ```bash
  python3 -m compileall src tests -q
  ```
- **Git State Hygiene:**
  ```bash
  git status
  ```

---

## 4. Coding & Execution Standards
- **Zero Laziness:** Write complete, test-backed implementations. No stubs, no `# TODO: implement later`, and no mock-only shortcuts.
- **Preserve Documentation Integrity:** Do not overwrite or strip existing docstrings, comments, or verified citation mappings.
- **Preserve File Links:** When referring to files in responses, use markdown links with the `file://` scheme.
