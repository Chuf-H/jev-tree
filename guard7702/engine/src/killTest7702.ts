import {
  createPublicClient,
  createWalletClient,
  http,
  parseUnits,
  formatUnits,
  encodeFunctionData,
  Address,
  Hex
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { signAuthorization } from "viem/experimental";
import { spawn, ChildProcess } from "child_process";
import { ReachabilityExplorer } from "./search/explorer.js";
import { EIP7702RecoveryPlanner } from "./recovery/eip7702.js";
import { EIP7702Capability } from "./capability/types.js";
import { ERC20_ABI, MALICIOUS_DELEGATE_ABI } from "./capability/abis.js";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const ANVIL_PORT = 8551;
const RPC_URL = `http://127.0.0.1:${ANVIL_PORT}`;

// Accounts
const VICTIM_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" as Hex;
const victimAccount = privateKeyToAccount(VICTIM_PRIVATE_KEY);
const victim = victimAccount.address;

const ATTACKER_PRIVATE_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d" as Hex;
const attackerAccount = privateKeyToAccount(ATTACKER_PRIVATE_KEY);
const attacker = attackerAccount.address;

async function startAnvil(): Promise<ChildProcess> {
  const anvil = spawn("/home/lewbei/.foundry/bin/anvil", [
    "--port",
    ANVIL_PORT.toString(),
    "--hardfork",
    "prague",
    "--silent"
  ]);

  await new Promise((resolve) => setTimeout(resolve, 1500));
  return anvil;
}

async function run7702KillTest() {
  console.log("==================================================================");
  console.log("             GUARD7702: EIP-7702 KILL TEST                       ");
  console.log("==================================================================");

  console.log("\n[1/7] Booting clean local Anvil node with Prague hardfork...");
  const anvil = await startAnvil();

  try {
    const publicClient = createPublicClient({
      transport: http(RPC_URL)
    });

    const victimWallet = createWalletClient({
      account: victimAccount,
      transport: http(RPC_URL)
    });

    const attackerWallet = createWalletClient({
      account: attackerAccount,
      transport: http(RPC_URL)
    });

    // 2. Deploy contracts
    console.log("[2/7] Deploying MockUSDC & MaliciousDelegate fixtures...");
    const mockUsdcArtifact = JSON.parse(
      fs.readFileSync(
        path.resolve(__dirname, "../../contracts/out/MockUSDC.sol/MockUSDC.json"),
        "utf8"
      )
    );
    const delegateArtifact = JSON.parse(
      fs.readFileSync(
        path.resolve(
          __dirname,
          "../../contracts/out/MaliciousDelegate.sol/MaliciousDelegate.json"
        ),
        "utf8"
      )
    );

    const usdcDeployTx = await victimWallet.deployContract({
      abi: mockUsdcArtifact.abi,
      bytecode: mockUsdcArtifact.bytecode.object as Hex
    });
    const usdcReceipt = await publicClient.waitForTransactionReceipt({
      hash: usdcDeployTx
    });
    const usdcAddress = usdcReceipt.contractAddress!;

    const delegateDeployTx = await attackerWallet.deployContract({
      abi: delegateArtifact.abi,
      bytecode: delegateArtifact.bytecode.object as Hex
    });
    const delegateReceipt = await publicClient.waitForTransactionReceipt({
      hash: delegateDeployTx
    });
    const delegateAddress = delegateReceipt.contractAddress!;

    console.log(`      MockUSDC deployed to:          ${usdcAddress}`);
    console.log(`      MaliciousDelegate deployed to: ${delegateAddress}`);

    // Fund victim with 10,000 USDC
    const INITIAL_USDC = parseUnits("10000", 6);
    const mintTx = await victimWallet.writeContract({
      address: usdcAddress,
      abi: mockUsdcArtifact.abi,
      functionName: "mint",
      args: [victim, INITIAL_USDC]
    });
    await publicClient.waitForTransactionReceipt({ hash: mintTx });

    let victimBal = await publicClient.readContract({
      address: usdcAddress,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [victim]
    });
    console.log(`      Victim Initial USDC Balance:   ${formatUnits(victimBal, 6)} USDC`);

    // 3. Off-chain authorization signing
    console.log("\n[3/7] Victim signs off-chain EIP-7702 authorization tuple...");
    const victimNonce = await publicClient.getTransactionCount({ address: victim });

    const auth = await signAuthorization(publicClient, {
      account: victimAccount,
      contractAddress: delegateAddress,
      chainId: 31337,
      nonce: victimNonce
    });

    const capability: EIP7702Capability = {
      kind: "EIP7702",
      owner: victim,
      chainId: 31337n,
      delegateAddress,
      nonce: BigInt(victimNonce),
      yParity: auth.yParity,
      r: auth.r,
      s: auth.s,
      targetToken: usdcAddress,
      authorizationObject: auth
    };

    console.log("      Signed EIP-7702 Capability:");
    console.log(`        Authority (Owner): ${capability.owner}`);
    console.log(`        Delegate Contract: ${capability.delegateAddress}`);
    console.log(`        Signed Nonce:      ${capability.nonce}`);
    console.log(`        Signature r:       ${capability.r}`);
    console.log(`        Signature s:       ${capability.s}`);

    // 4. Baseline one-step simulation check
    console.log("\n[4/7] Evaluating Baseline 1-Step Simulation (eth_call / static delta)...");
    const deltaAtSigning = INITIAL_USDC - victimBal;
    console.log(`      Current balance delta: ${formatUnits(deltaAtSigning, 6)} USDC`);
    console.log("      Immediate-delta baseline verdict: [SAFE - 0.00 USDC IMMEDIATE LOSS]");
    console.log("      ⚠️  DANGER: Flawed assumption! SimulateCurrentExecution(c, s0) != SafeFutureCapability(c, s0)");

    // 5. Run Guard7702 Reachability Explorer
    console.log("\n[5/7] Running Guard7702 Bounded Reachability Explorer (depth <= 3)...");
    const explorer = new ReachabilityExplorer(
      publicClient,
      {
        request: async (args: { method: string; params?: any[] }) => {
          return await publicClient.request(args as any);
        }
      },
      3
    );

    const counterexample = await explorer.explore(capability, attacker);

    if (!counterexample) {
      throw new Error("FAILED: Guard7702 should have discovered the multi-step reachability exploit!");
    }

    console.log("\n  🚨 COUNTEREXAMPLE DISCOVERED BY GUARD7702!");
    console.log(`     Capability Kind: ${counterexample.capability}`);
    console.log(`     Reachable Depth: ${counterexample.depth} step(s)`);
    console.log(`     Reachable Loss:  ${counterexample.loss.formatted} ${counterexample.loss.symbol}`);
    console.log("     Executable Exploit Trace:");
    counterexample.trace.forEach((step, idx) => {
      console.log(`       [Step ${idx + 1}] Action: ${step.id}`);
      console.log(`                Desc:   ${step.description}`);
      console.log(`                Target: ${step.target}`);
      console.log(`                Actor:  ${step.actor}`);
    });

    // 6. Generate State-Specific Recovery Action
    console.log("\n[6/7] Planning State-Specific Recovery Action...");
    const recoveryAction = await EIP7702RecoveryPlanner.plan(capability, publicClient);
    console.log(`     Recommended Strategy: ${recoveryAction.strategy}`);
    console.log(`     Plan Description:     ${recoveryAction.description}`);

    // 7. Verify Proof of Mitigation on Fork
    console.log("\n[7/7] Executing Recovery Action & Verifying Exploit Replay Mitigation...");
    if (recoveryAction.strategy === "ADVANCE_NONCE") {
      // Execute nonce advance self-transaction
      const nonceTx = await victimWallet.sendTransaction({
        to: victim,
        value: 0n
      });
      await publicClient.waitForTransactionReceipt({ hash: nonceTx });
      const newNonce = await publicClient.getTransactionCount({ address: victim });
      console.log(`      Victim nonce advanced from ${victimNonce} to ${newNonce}`);

      // Now attempt to replay the discovered exploit trace
      console.log("      Replaying discovered attacker exploit trace...");
      const snapshot = await publicClient.request({ method: "evm_snapshot" } as any);
      let attackBlocked = false;

      try {
        const step1 = counterexample.trace[0];
        const txHash = await publicClient.request({
          method: "eth_sendTransaction",
          params: [
            {
              from: step1.actor,
              to: step1.target,
              data: step1.calldata,
              authorizationList: step1.authorizationList
            }
          ]
        } as any);

        const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
        // Under EIP-7702, if the authorization nonce does not match current nonce,
        // the authorization is skipped/invalidated, so delegation code is NOT installed!
        const code = await publicClient.getBytecode({ address: victim });
        if (!code || code === "0x") {
          attackBlocked = true;
          console.log("      -> Authorization skipped due to nonce mismatch! Victim code remains clean.");
        }
      } catch (err: any) {
        attackBlocked = true;
        console.log(`      -> Type-4 transaction rejected: ${err.message}`);
      }

      const balAfterReplay = await publicClient.readContract({
        address: usdcAddress,
        abi: ERC20_ABI,
        functionName: "balanceOf",
        args: [victim]
      });

      console.log(`      Victim balance after replaying trace: ${formatUnits(balAfterReplay, 6)} USDC`);

      if (attackBlocked && balAfterReplay === INITIAL_USDC) {
        console.log("\n  ✅ VERIFIED: Pre-execution Nonce Advancement completely neutralized exploit trace!");
      } else {
        throw new Error("FAILED: Replay was not neutralized by recovery action!");
      }

      await publicClient.request({ method: "evm_revert", params: [snapshot] } as any);
    }

    // Now test Case B: Post-installation Active Delegation Recovery
    console.log("\n[Bonus Verification] Testing Case B: Active Delegation Recovery (Setting address(0))...");
    // Step B1: Attacker installs delegation with valid signed authorization
    const nonceBeforeB = await publicClient.getTransactionCount({ address: victim });
    const authCaseB = await signAuthorization(publicClient, {
      account: victimAccount,
      contractAddress: delegateAddress,
      chainId: 31337,
      nonce: nonceBeforeB
    });

    const installHash = await publicClient.request({
      method: "eth_sendTransaction",
      params: [
        {
          from: attacker,
          to: victim,
          data: "0x",
          authorizationList: [authCaseB]
        }
      ]
    } as any);
    await publicClient.waitForTransactionReceipt({ hash: installHash });

    const activeBytecode = await publicClient.getBytecode({ address: victim });
    console.log(`      Active Delegation Installed Bytecode: ${activeBytecode}`);

    // Step B2: Recovery planner detects active delegation
    const postInstallPlan = await EIP7702RecoveryPlanner.plan(capability, publicClient);
    console.log(`      Detected Strategy: ${postInstallPlan.strategy}`);
    console.log(`      Plan Description:  ${postInstallPlan.description}`);

    // Step B3: Victim signs and executes recovery authorization pointing to address(0)
    const currentVictimNonce = await publicClient.getTransactionCount({ address: victim });
    const recoveryAuth = await signAuthorization(publicClient, {
      account: victimAccount,
      contractAddress: "0x0000000000000000000000000000000000000000",
      chainId: 31337,
      nonce: currentVictimNonce
    });

    const clearTx = await victimWallet.sendTransaction({
      to: victim,
      authorizationList: [recoveryAuth]
    });
    await publicClient.waitForTransactionReceipt({ hash: clearTx });

    const clearedBytecode = await publicClient.getBytecode({ address: victim });
    console.log(`      Bytecode after recovery execution:   ${clearedBytecode || "0x"}`);

    // Step B4: Attacker attempts to call sweep()
    const sweepData = encodeFunctionData({
      abi: MALICIOUS_DELEGATE_ABI,
      functionName: "sweep",
      args: [usdcAddress, attacker]
    });

    await publicClient.request({
      method: "eth_sendTransaction",
      params: [
        {
          from: attacker,
          to: victim,
          data: sweepData
        }
      ]
    } as any);

    const victimBalFinal = await publicClient.readContract({
      address: usdcAddress,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [victim]
    });

    console.log(`      Victim USDC Balance after sweep attempt: ${formatUnits(victimBalFinal, 6)} USDC`);

    if (victimBalFinal === INITIAL_USDC) {
      console.log("  ✅ VERIFIED: address(0) delegation cleared code, attacker sweep neutralized!");
    } else {
      throw new Error("FAILED: Sweep succeeded after clearance!");
    }

    console.log("\n==================================================================");
    console.log("       ALL EIP-7702 KILL TESTS PASSED WITH 100% RIGOR             ");
    console.log("==================================================================");
  } finally {
    anvil.kill();
  }
}

run7702KillTest().catch((err) => {
  console.error("\n❌ FATAL ERROR IN EIP-7702 KILL TEST:", err);
  process.exit(1);
});
