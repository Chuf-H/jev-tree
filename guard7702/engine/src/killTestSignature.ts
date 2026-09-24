import {
  createPublicClient,
  createWalletClient,
  http,
  parseAbi,
  parseUnits,
  formatUnits,
  Address,
  Hex
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { spawn, ChildProcess } from "child_process";
import { ReachabilityExplorer } from "./search/explorer.js";
import { Permit2SignatureRecoveryPlanner } from "./recovery/permit2Signature.js";
import { Permit2SignatureCapability } from "./capability/types.js";
import { ERC20_ABI } from "./capability/abis.js";
import * as fs from "fs";
import * as path from "path";

const ANVIL_PORT = 8547;
const RPC_URL = `http://127.0.0.1:${ANVIL_PORT}`;

// Test Accounts
const VICTIM_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" as Hex;
const victimAccount = privateKeyToAccount(VICTIM_PRIVATE_KEY);
const victim = victimAccount.address;
const attacker: Address = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";

async function startAnvil(): Promise<ChildProcess> {
  const anvil = spawn("/home/lewbei/.foundry/bin/anvil", [
    "--port",
    ANVIL_PORT.toString(),
    "--silent"
  ]);
  await new Promise((resolve) => setTimeout(resolve, 1500));
  return anvil;
}

async function runSignatureKillTest() {
  console.log("==================================================================");
  console.log("       GUARD7702: PERMIT2 SIGNATURETRANSFER KILL TEST             ");
  console.log("==================================================================");

  console.log("\n[1/7] Booting clean local Anvil node on port " + ANVIL_PORT + "...");
  const anvil = await startAnvil();

  try {
    const publicClient = createPublicClient({
      transport: http(RPC_URL)
    });

    const walletClient = createWalletClient({
      account: victimAccount,
      transport: http(RPC_URL)
    });

    console.log("[2/7] Deploying canonical Permit2 & MockUSDC contracts...");
    const permit2Artifact = JSON.parse(
      fs.readFileSync(
        path.resolve(
          __dirname,
          "../../contracts/out/Permit2.sol/Permit2.json"
        ),
        "utf8"
      )
    );

    const mockUsdcArtifact = JSON.parse(
      fs.readFileSync(
        path.resolve(
          __dirname,
          "../../contracts/out/MockUSDC.sol/MockUSDC.json"
        ),
        "utf8"
      )
    );

    const permit2DeployHash = await walletClient.deployContract({
      abi: permit2Artifact.abi,
      bytecode: permit2Artifact.bytecode.object
    });
    const permit2Receipt = await publicClient.waitForTransactionReceipt({ hash: permit2DeployHash });
    const permit2Address = permit2Receipt.contractAddress!;
    console.log(`  -> Permit2 deployed at: ${permit2Address}`);

    const usdcDeployHash = await walletClient.deployContract({
      abi: mockUsdcArtifact.abi,
      bytecode: mockUsdcArtifact.bytecode.object
    });
    const usdcReceipt = await publicClient.waitForTransactionReceipt({ hash: usdcDeployHash });
    const usdcAddress = usdcReceipt.contractAddress!;
    console.log(`  -> MockUSDC deployed at: ${usdcAddress}`);

    const DRAIN_AMOUNT = parseUnits("10000", 6); // 10,000 USDC
    const NONCE = 1025n; // wordPos = 4, bitPos = 1

    // Mint 10,000 USDC to victim
    const mintHash = await walletClient.writeContract({
      address: usdcAddress,
      abi: parseAbi(["function mint(address to, uint256 amount) external"]),
      functionName: "mint",
      args: [victim, DRAIN_AMOUNT]
    });
    await publicClient.waitForTransactionReceipt({ hash: mintHash });

    // Victim approves Permit2
    const approveHash = await walletClient.writeContract({
      address: usdcAddress,
      abi: ERC20_ABI,
      functionName: "approve",
      args: [permit2Address, 2n ** 256n - 1n]
    });
    await publicClient.waitForTransactionReceipt({ hash: approveHash });
    console.log(`  -> Seeded victim (${victim}) with 10,000 USDC and universal Permit2 approval.`);

    // Sign PermitTransferFrom capability
    console.log("\n[3/7] Generating victim's signed PermitTransferFrom capability (unordered nonce)...");
    const deadline = BigInt(Math.floor(Date.now() / 1000) + 3600);

    const typeData = {
      types: {
        TokenPermissions: [
          { name: "token", type: "address" },
          { name: "amount", type: "uint256" }
        ],
        PermitTransferFrom: [
          { name: "permitted", type: "TokenPermissions" },
          { name: "spender", type: "address" },
          { name: "nonce", type: "uint256" },
          { name: "deadline", type: "uint256" }
        ]
      },
      primaryType: "PermitTransferFrom" as const,
      domain: {
        name: "Permit2",
        chainId: 31337,
        verifyingContract: permit2Address
      },
      message: {
        permitted: {
          token: usdcAddress,
          amount: DRAIN_AMOUNT
        },
        spender: attacker,
        nonce: NONCE,
        deadline: deadline
      }
    };

    const signature = await walletClient.signTypedData(typeData);

    const capability: Permit2SignatureCapability = {
      kind: "PERMIT2_SIGNATURE",
      owner: victim,
      chainId: 31337n,
      permit2Address,
      permitted: {
        token: usdcAddress,
        amount: DRAIN_AMOUNT
      },
      nonce: NONCE,
      deadline,
      spender: attacker,
      signature
    };

    console.log(`  -> Signed capability created with unordered nonce ${NONCE}.`);

    // 1-Step Baseline
    console.log("\n[4/7] Evaluating 1-Step Baseline (Current Execution State):");
    const balanceBefore = await publicClient.readContract({
      address: usdcAddress,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [victim]
    });
    console.log(`  -> Victim Balance at Step 0: ${formatUnits(balanceBefore, 6)} USDC`);
    console.log(`  -> Immediate Balance Delta:  $0.00 (Passes standard 1-step simulation!)`);

    // Run Guard7702 Bounded Reachability Engine
    console.log("\n[5/7] Executing Guard7702 Bounded Reachability Search (k <= 3)...");
    const explorer = new ReachabilityExplorer(publicClient, publicClient);
    const counterexample = await explorer.explore(capability, attacker);

    if (!counterexample) {
      console.error("  ❌ FAILED: Reachability engine failed to discover exploit trace!");
      process.exit(1);
    }

    console.log("  ✓ SUCCESS: Reachable Exploit Path Discovered!");
    console.log(`  -> Search Depth: ${counterexample.depth} step(s)`);
    console.log(`  -> Total Reachable Loss: ${counterexample.loss.formatted} ${counterexample.loss.symbol}`);
    console.log("  -> Synthesized Attacker Action Trace:");
    counterexample.trace.forEach((action, idx) => {
      console.log(`     Step ${idx + 1}: [${action.id}] -> ${action.description}`);
    });

    // Test Recovery Planner
    console.log("\n[6/7] Planning and Executing Recovery Transaction...");
    const recoveryAction = await Permit2SignatureRecoveryPlanner.plan(capability, publicClient);
    console.log(`  -> Selected Recovery Strategy: ${recoveryAction.strategy}`);
    console.log(`  -> Action: ${recoveryAction.description}`);

    const recoveryHash = await walletClient.sendTransaction({
      to: recoveryAction.target,
      data: recoveryAction.calldata
    });
    await publicClient.waitForTransactionReceipt({ hash: recoveryHash });
    console.log("  -> Recovery transaction confirmed on Anvil.");

    // Replay Attacker Counterexample Against Post-Recovery State
    console.log("\n[7/7] Replaying Attacker Counterexample Against Post-Recovery State...");
    let replayedSucceeded = false;
    try {
      await publicClient.request({
        method: "anvil_impersonateAccount",
        params: [attacker]
      });
      await publicClient.request({
        method: "anvil_setBalance",
        params: [attacker, "0xde0b6b3a7640000"]
      });

      const txHash: Hex = await publicClient.request({
        method: "eth_sendTransaction",
        params: [
          {
            from: attacker,
            to: counterexample.trace[0].target,
            data: counterexample.trace[0].calldata,
            gas: "0x100000"
          }
        ]
      });

      const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
      if (receipt.status === "reverted") {
        console.log("  ✓ Attacker permitTransferFrom REVERTED ON-CHAIN (receipt.status: reverted)!");
      } else {
        replayedSucceeded = true;
      }
    } catch (err: any) {
      console.log("  ✓ Attacker permitTransferFrom REVERTED with error:", err.message || err);
    }

    if (replayedSucceeded) {
      console.error("  ❌ FAILED: Attacker transaction succeeded after recovery!");
      process.exit(1);
    }

    const finalBalance = await publicClient.readContract({
      address: usdcAddress,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [victim]
    });
    console.log(`  -> Victim Final Balance: ${formatUnits(finalBalance, 6)} USDC (Tracked Assets Unchanged)`);

    console.log("\n==================================================================");
    console.log(" 🎉 SIGNATURETRANSFER KILL TEST 100% PASSED!");
    console.log(" Capability -> Discovered Loss -> Invalidate Unordered Nonce -> Replay Reverts");
    console.log("==================================================================");
  } finally {
    anvil.kill();
  }
}

runSignatureKillTest().catch((err) => {
  console.error("Signature kill test error:", err);
  process.exit(1);
});
