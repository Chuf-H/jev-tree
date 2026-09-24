# Guard7702: Capability-Aware Multi-Step Reachability Verifier

[![Foundry Tests](https://img.shields.io/badge/Foundry-13%2F13%20Passing-emerald)](./contracts)
[![TypeScript Engine](https://img.shields.io/badge/Engine-3%2F3%20Kill%20Tests%20Passing-cyan)](./engine)
[![EIP-7702](https://img.shields.io/badge/EIP--7702-Prague%20Hardfork-blue)](https://eips.ethereum.org/EIPS/eip-7702)
[![Uniswap Permit2](https://img.shields.io/badge/Uniswap-Permit2%20Allowance%20%26%20Signature-purple)](https://github.com/Uniswap/permit2)

> **Guard7702** is a typed, executable capability-reachability verifier and state-specific recovery synthesizer for Ethereum Prague EIP-7702 authorizations and Uniswap Permit2 signatures.
>
> Built for the **3rd-Web-Hack Hackathon** (TechZap Club, Sept 2026).

---

## 1. The Core Problem: The Single-Step Simulation Fallacy

Conventional execution simulation answers what a proposed execution does under a particular current state. Detached authorization capabilities introduce a fundamentally different question: **what future attacker-controlled state transitions become reachable after the signed capability is released?**

### The Mathematical Flaw
$$\boxed{\text{SimulateCurrentExecution}(c, s_0) \not\Rightarrow \text{SafeFutureCapability}(c, s_0)}$$

1. **Detached Authorization:** The signing event is physically detached from its eventual on-chain consumption. The authorization can later be included in an EIP-7702 Type-0x04 transaction or a Permit2 permit call.
2. **Zero Immediate Delta:** When an end-user signs an off-chain authorization, **zero state changes occur on-chain**. An immediate-delta baseline evaluates:
   $$\Delta \text{Balance}(s_0) = \$0.00$$
3. **Dangerous False Negatives:** An authorization may cause zero immediate state change while enabling a later loss-producing execution path. An immediate-delta baseline marks the capability as safe purely because no balance moved at Step 0.
4. **Asynchronous Drainage:** An attacker subsequently relays the signed authorization in a Type-0x04 transaction or Permit2 call, installs malicious delegation code or allowance, and executes a multi-step asset drain.

Recent empirical research highlights this threat surface:
- **USENIX Security 2026 (Huang et al.):** USENIX Security 2026 reports that, across its seven-chain dataset, over **63% of observed EIP-7702 authorization transactions** were associated with malicious EOA-targeted attacks (identifying 924 malicious contract accounts, >$2.3M in realized losses, and >$10M exposed).

---

## 2. The Guard7702 Solution

Instead of assuming arbitrary protocol semantics or relying on simple blacklist pattern matching, Guard7702 models off-chain capabilities as **reachable state transitions** on a forked EVM environment (Anvil):

```text
Signed Capability (c)
       │
       ▼
Capability Decoder: Decode(c)
       │
       ▼
Action Generator: Legal Candidate Transitions 𝒜_modeled(c, s)
       │
       ▼
Bounded DFS Reachability Explorer (k ≤ 3, Anvil Snapshots)
       │
       ├──► No Loss Path Found:
       │    No modeled loss path found within supported action semantics and depth k ≤ 3
       │    (¬Unsafe_{≤ k}^{𝒜_modeled}(c, s₀))
       │
       └──► Loss Path Found:
            Concrete Vulnerability Witness π = (a₁, ..., aⱼ), j ≤ 3 s.t. L(s₀, T_π(s₀)) > 0
                    │
                    ▼
            Synthesize State-Specific Recovery Action: Recovery(c, s) ⟶ s_R
                    │
                    ▼
            Execute Recovery Action On Fork
                    │
                    ▼
       Replay Discovered Exploit Trace Replay(π, s_R) ──► REVERTS ON-CHAIN
       (Tracked asset balances unchanged under replayed trace)
```

### Formal Verification Asymmetry

Guard7702 enforces an explicit, asymmetric verification contract:

$$\boxed{\text{Found loss path } \pi \implies \text{concrete vulnerability witness under fork state } s_0}$$

$$\boxed{\text{No path found} \not\implies \text{globally safe (establishes } \neg Unsafe_{\le k}^{\mathcal A_{\text{modeled}}}(c,s_0) \text{ only)}}$$

- **Positive Counterexample:** When a path $\pi = (a_1, \ldots, a_j)$ is discovered at depth $j \le 3$, Guard7702 executes each step on a live Anvil fork and measures $L(s_0, T_\pi(s_0)) > 0$. This provides a concrete, executable counterexample witness proving the capability is unsafe under current fork state $s_0$.
- **Bounded Verification Limit:** When no loss path is found, Guard7702 proves only that no loss trace exists within the supported action generators $\mathcal A_{\text{modeled}}$ and depth bound $k \le 3$. The depth bound $k \le 3$ captures canonical multi-step exploit sequences (Step 1: capability relay/permit injection $\to$ Step 2: asset transfer/sweep $\to$ Step 3: optional vault unwind/intermediate transfer) while keeping Anvil EVM snapshot branching linear and under ~2 seconds. This avoids heuristic "risk scores" while remaining mathematically honest: it is not a proof of global safety against unmodeled actions or deeper sequences ($k > 3$).
- **Loss Metric Definition:** The loss function evaluates the net reduction in victim assets across state transitions:
  $$L(s_0, s') = \sum_{t \in \text{Tracked}} \max(0, \text{Balance}_{t,\text{victim}}(s_0) - \text{Balance}_{t,\text{victim}}(s'))$$
  monitoring native ETH and tracked ERC-20 token balances for the victim EOA.
- **Action Space Bounds ($\mathcal{A}_{\text{modeled}}$):**
  - *In Scope / Modeled:* Canonical Permit2 `permit` and `permitTransferFrom` invocations, EIP-7702 Type-0x04 delegation relays, and direct token drain / delegation `sweep` calls.
  - *Out of Scope / Future Work:* Arbitrary external DeFi composability (flash-loan-assisted liquidations, multi-hop DEX arbitrage, nested protocol reentrancy).

### Supported Capability Semantics & Recovery Mechanisms

1. **EIP-7702 Authorization Tuples (Prague Hardfork):**
   - *Exploration:* Attacker relays authorization tuple via an EIP-7702 Type-0x04 transaction installing `0xef0100 || delegateAddress` $\to$ Attacker calls `sweep()` in victim EOA context.
   - *Recovery:*
     - **Unconsumed Authorization:** Advances victim account nonce via a 0-value self-transaction. Under EIP-7702 rules (`authority.nonce == auth.nonce`), the stolen authorization tuple is **invalidated by nonce mismatch**.
     - **Active Delegation:** Generates a Type-0x04 recovery transaction with authorization pointing to `address(0)`, immediately clearing the delegation indicator back to a clean EOA.
     - *Race Condition Qualification:* Neutralization is strictly subject to mining order:
       $$\text{Recovery mined first} \implies \text{old authorization invalid by nonce mismatch}$$
       If an attacker transaction consumes the authorization before recovery is mined, the malicious transition may land first.
     - *Operational Recommendation:* To mitigate frontrunning risk in public mempools, recovery transactions (such as nonce advance or Permit2 invalidation) should be broadcast via private RPC endpoints (e.g., Flashbots Protect or MEV-share). In adversarial public mempools, the self-transaction must be submitted with elevated `maxPriorityFeePerGas` (EIP-1559 replacement pricing) to prevent transaction underpricing or eviction.
2. **Uniswap Permit2 AllowanceTransfer:**
   - *Exploration:* Attacker broadcasts `PermitSingle` payload $\to$ Attacker calls `Permit2.transferFrom()`.
   - *Recovery:* Calls canonical `Permit2.invalidateNonces(token, spender, newNonce)` operating on caller identity, advancing the nonce to neutralize the permit before broadcast; or calls `Permit2.lockdown()` to zero active allowances.
3. **Uniswap Permit2 SignatureTransfer:**
   - *Exploration:* Attacker executes `Permit2.permitTransferFrom()` via signed unordered nonce bitmap.
   - *Recovery:* Calls canonical `Permit2.invalidateUnorderedNonces(wordPos, mask)` operating on caller identity to flip the target bitmap bit, permanently invalidating the nonce.

---

## 3. Architecture & Repository Structure

```
guard7702/
├── contracts/                  # Solidity smart contracts & Foundry test suites
│   ├── src/
│   │   ├── MockUSDC.sol        # Solmate ERC20 test token fixture
│   │   ├── MaliciousDelegate.sol # Controlled EIP-7702 drain fixture (sweep/execute)
│   │   └── Guard7702Sentinel.sol # Batch invalidation & emergency recovery sentinel
│   ├── test/
│   │   ├── Permit2Allowance.t.sol # 4/4 PASS (Multi-step drain & recovery)
│   │   ├── Permit2Signature.t.sol # 3/3 PASS (Unordered nonce drain & recovery)
│   │   ├── EIP7702Attack.t.sol    # 4/4 PASS (Prague Type-4 relay, nonce advance, address(0) clear)
│   │   └── Guard7702Sentinel.t.sol # 2/2 PASS (Delegated context batch invalidations)
│   └── foundry.toml            # Solc 0.8.17, via_ir = true, Prague EVM settings
│
├── engine/                     # TypeScript Capability-Reachability Engine
│   ├── src/
│   │   ├── capability/
│   │   │   ├── types.ts        # Typed Capability, Action, SearchNode, Counterexample
│   │   │   └── abis.ts         # Permit2, ERC20, and MaliciousDelegate ABIs
│   │   ├── semantics/
│   │   │   ├── permit2Allowance.ts # Permit2 AllowanceTransfer action generator
│   │   │   ├── permit2Signature.ts # Permit2 SignatureTransfer action generator
│   │   │   └── eip7702.ts          # EIP-7702 Type-4 relay & sweep action generator
│   │   ├── recovery/
│   │   │   ├── permit2.ts          # Nonce invalidation & allowance lockdown planner
│   │   │   ├── permit2Signature.ts # Unordered nonce bitmap invalidation planner
│   │   │   └── eip7702.ts          # Nonce advance & address(0) delegation planner
│   │   ├── search/
│   │   │   └── explorer.ts     # Bounded DFS explorer using Anvil snapshots & reverts
│   │   ├── killTest.ts         # Permit2 AllowanceTransfer end-to-end kill test (100% PASS)
│   │   ├── killTestSignature.ts# Permit2 SignatureTransfer end-to-end kill test (100% PASS)
│   │   └── killTest7702.ts     # EIP-7702 Prague Hardfork end-to-end kill test (100% PASS)
│   └── package.json
│
└── app/                        # Interactive Next/Vite React Proof Visualizer Dashboard
    ├── src/
    │   ├── App.tsx             # Interactive scenario runner, graph visualizer & replay tester
    │   └── index.css           # Tailwind CSS v4 cyberpunk/fintech dark theme
    └── package.json
```

---

## 4. Quickstart & Verification Reproduction

### Prerequisites
- Node.js >= 18 (`node -v`)
- Foundry / Anvil >= 1.8 (`forge --version`, `anvil --version`)
- *Offline Execution:* All test suites and benchmarks execute completely offline against local Anvil state without requiring external RPC keys or mainnet connectivity.

### Step 1: Run Foundry Solidity Test Suite
Verify all 13 smart contract security tests covering baseline delta, multi-step exploit execution, and mitigation:

```bash
cd guard7702/contracts
forge test -v
```

Expected output:
```
Ran 3 tests for test/Permit2Signature.t.sol:Permit2SignatureTest   (3 passed)
Ran 4 tests for test/Permit2Allowance.t.sol:Permit2AllowanceTest   (4 passed)
Ran 4 tests for test/EIP7702Attack.t.sol:EIP7702AttackTest         (4 passed)
Ran 2 tests for test/Guard7702Sentinel.t.sol:Guard7702SentinelTest (2 passed)
Suite result: ok. 13 passed; 0 failed; 0 skipped
```

### Step 2: Run TypeScript Reachability Engine Kill Tests
Run the 3 automated kill tests. Each test script automatically spawns, orchestrates, and tears down ephemeral local Anvil child processes (with Prague hardfork for EIP-7702; requires `anvil` in `$PATH`), executes the multi-step reachability discovery, generates the recovery transaction, and proves on-fork that exploit replay reverts:

```bash
cd guard7702/engine

# 1. Permit2 AllowanceTransfer Kill Test
npx tsx src/killTest.ts

# 2. Permit2 SignatureTransfer Kill Test
npx tsx src/killTestSignature.ts

# 3. EIP-7702 Prague Hardfork Kill Test
npx tsx src/killTest7702.ts
```

### Step 3: Run Interactive Proof Visualizer Dashboard
Launch the interactive web UI to inspect signed capabilities, view the immediate-delta baseline comparison, explore the reachability graph, and trigger on-fork recovery execution (runs self-contained with verified pre-computed fixtures for instant offline evaluation, with live local Anvil RPC integration points):

```bash
cd guard7702/app
npm install
npm run dev
```
Open `http://localhost:5173` in your browser.

---

## 5. Formal Stopping Criterion

Guard7702 enforces a deterministic executable stopping criterion against live EVM state snapshots:

$$\boxed{L(s_0, T_\pi(s_0)) > 0 \quad\land\quad Replay(\pi, s_R) \text{ fails}}$$

Every verified capability counterexample satisfies:
1. **Immediate-Delta Baseline:** Immediate balance change at depth 0 is proven to be $\Delta = \$0.00$ (demonstrating the false negative of current-state simulation).
2. **Positive Counterexample:** Reachability explorer successfully discovers an executable exploit trace $\pi$ with $L(s_0, T_\pi(s_0)) > 0$ on forked state.
3. **Recovery Construction:** Engine synthesizes the exact, state-specific recovery transaction $s \to s_R$.
4. **Deterministic Executable Verification:** Guard7702 replays the identical counterexample $\pi$ against the post-recovery fork state $s_R$ and verifies that the previously successful exploit trace now reverts, keeping tracked asset balances unchanged under the replayed trace.

---

## 6. Academic References & Citations

1. **Huang, M., et al. (USENIX Security 2026).** *Revealing the Dark Side of Smart Accounts: An Empirical Study of EIP-7702 Incurred Risks in Blockchain Ecosystem*. USENIX Security Symposium.
2. **Hauser, M. (2026).** *Write-Domain Separation and Non-Custodial Enforcement: A Structural Impossibility in Account-Based Ledgers, with a Commitment-Based Construction*. arXiv:2605.01210.
3. **Ethereum Foundation.** *EIP-7702: Set EOA account code for one transaction*. [EIPs Repository](https://eips.ethereum.org/EIPS/eip-7702).
4. **Uniswap Labs.** *Permit2: Signature-based token approvals and transfers*. [GitHub](https://github.com/Uniswap/permit2).
5. **Blockaid Security Research (2025).** *Signature Harvesting and Delayed Drainer Exploits in Web3*.

---

## 7. Research & Safety Disclaimer

*Guard7702 is a hackathon research prototype and bounded reachability verifier. While recovery transactions deterministically neutralize exploit replays on forked EVM snapshots, live mainnet mitigations operate in adversarial mempools subject to miner extraction and gas auction dynamics. In live production environments, recovery transactions should be dispatched via private RPC endpoints (e.g., Flashbots Protect).*
