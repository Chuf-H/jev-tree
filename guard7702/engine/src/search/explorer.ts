import {
  PublicClient,
  formatUnits,
  Hex,
  Hash
} from "viem";
import { Action, Counterexample, Capability, Permit2AllowanceCapability, Permit2SignatureCapability, EIP7702Capability } from "../capability/types.js";
import { Permit2AllowanceSemantics } from "../semantics/permit2Allowance.js";
import { Permit2SignatureSemantics } from "../semantics/permit2Signature.js";
import { EIP7702Semantics } from "../semantics/eip7702.js";
import { ERC20_ABI } from "../capability/abis.js";

export interface AnvilRpcClient {
  request(args: { method: string; params?: any[] }): Promise<any>;
}

export class ReachabilityExplorer {
  constructor(
    private publicClient: PublicClient,
    private rpcClient: AnvilRpcClient,
    private maxDepth: number = 3
  ) {}

  async explore(
    capability: Capability,
    attacker: `0x${string}`
  ): Promise<Counterexample | null> {
    const owner = capability.owner;
    const token = this.getTokenFromCapability(capability);

    const initialBalance: bigint = await this.publicClient.readContract({
      address: token,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [owner]
    });

    const symbol: string = await this.publicClient.readContract({
      address: token,
      abi: ERC20_ABI,
      functionName: "symbol"
    });

    const decimals: number = await this.publicClient.readContract({
      address: token,
      abi: ERC20_ABI,
      functionName: "decimals"
    });

    return this.dfs(0, [], initialBalance, capability, attacker, symbol, decimals);
  }

  private getTokenFromCapability(capability: Capability): `0x${string}` {
    if (capability.kind === "PERMIT2_ALLOWANCE") {
      return capability.details.token;
    } else if (capability.kind === "PERMIT2_SIGNATURE") {
      return capability.permitted.token;
    } else if (capability.kind === "EIP7702") {
      if (!capability.targetToken) {
        throw new Error("EIP7702Capability requires targetToken for reachability verification");
      }
      return capability.targetToken;
    }
    throw new Error(`Unsupported capability kind: ${(capability as any).kind}`);
  }

  private async dfs(
    depth: number,
    trace: Action[],
    initialBalance: bigint,
    capability: Capability,
    attacker: `0x${string}`,
    symbol: string,
    decimals: number
  ): Promise<Counterexample | null> {
    if (depth >= this.maxDepth) return null;

    const owner = capability.owner;
    const token = this.getTokenFromCapability(capability);

    // Enumerate candidate actions from semantic models
    let actions: Action[] = [];
    if (capability.kind === "PERMIT2_ALLOWANCE") {
      actions = await Permit2AllowanceSemantics.enumerateActions(
        capability,
        this.publicClient,
        attacker
      );
    } else if (capability.kind === "PERMIT2_SIGNATURE") {
      actions = await Permit2SignatureSemantics.enumerateActions(
        capability,
        this.publicClient,
        attacker
      );
    } else if (capability.kind === "EIP7702") {
      actions = await EIP7702Semantics.enumerateActions(
        capability,
        this.publicClient,
        attacker
      );
    }

    console.log(`     [Explorer Depth ${depth}] Discovered ${actions.length} legal action(s): ${actions.map(a => a.id).join(", ")}`);

    for (const action of actions) {
      // 1. Take snapshot for backtrack
      const snapshot: Hex = await this.rpcClient.request({ method: "evm_snapshot" });

      try {
        // 2. Execute candidate action
        await this.executeAction(action);

        // 3. Inspect state after action execution
        const currentBalance: bigint = await this.publicClient.readContract({
          address: token,
          abi: ERC20_ABI,
          functionName: "balanceOf",
          args: [owner]
        });

        console.log(`       -> Executed [${action.id}]. Victim balance: ${formatUnits(currentBalance, decimals)} ${symbol}`);

        // Check if invariant broken (loss > 0)
        if (currentBalance < initialBalance) {
          const lossAmount = initialBalance - currentBalance;
          // Counterexample found!
          await this.rpcClient.request({ method: "evm_revert", params: [snapshot] });

          return {
            capability: capability.kind,
            owner,
            attacker,
            depth: depth + 1,
            trace: [...trace, action],
            loss: {
              token,
              symbol,
              amount: lossAmount.toString(),
              formatted: formatUnits(lossAmount, decimals)
            }
          };
        }

        // 4. Recurse deeper
        const deeperResult = await this.dfs(
          depth + 1,
          [...trace, action],
          initialBalance,
          capability,
          attacker,
          symbol,
          decimals
        );

        // 5. Backtrack
        await this.rpcClient.request({ method: "evm_revert", params: [snapshot] });

        if (deeperResult) {
          return deeperResult;
        }
      } catch (err: any) {
        console.log(`       -> Action [${action.id}] reverted: ${err.message || err}`);
        await this.rpcClient.request({ method: "evm_revert", params: [snapshot] });
      }
    }

    return null;
  }

  private async executeAction(action: Action): Promise<void> {
    // Impersonate the actor on Anvil
    await this.rpcClient.request({
      method: "anvil_impersonateAccount",
      params: [action.actor]
    });

    // Fund actor with 1 ETH for gas if needed
    await this.rpcClient.request({
      method: "anvil_setBalance",
      params: [action.actor, "0xde0b6b3a7640000"] // 1 ETH
    });

    // Send transaction
    const txParams: any = {
      from: action.actor,
      to: action.target,
      data: action.calldata,
      gas: "0x100000"
    };
    if (action.authorizationList && action.authorizationList.length > 0) {
      txParams.authorizationList = action.authorizationList;
    }

    const txHash: Hash = await this.rpcClient.request({
      method: "eth_sendTransaction",
      params: [txParams]
    });

    const receipt = await this.publicClient.waitForTransactionReceipt({ hash: txHash });
    if (receipt.status !== "success") {
      throw new Error(`Transaction ${action.id} reverted on-chain (status: ${receipt.status})`);
    }
  }
}
