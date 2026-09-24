import { encodeFunctionData, PublicClient, Hex, Address } from "viem";
import { Permit2AllowanceCapability } from "../capability/types.js";
import { PERMIT2_ABI } from "../capability/abis.js";

export interface RecoveryAction {
  strategy: "INVALIDATE_NONCE" | "LOCKDOWN_ALLOWANCE" | "NOOP";
  description: string;
  target: Address;
  calldata: Hex;
  actor: Address; // Must be owner because Permit2 checks msg.sender
}

export class Permit2RecoveryPlanner {
  static async plan(
    capability: Permit2AllowanceCapability,
    client: PublicClient
  ): Promise<RecoveryAction> {
    const owner = capability.owner;
    const token = capability.details.token;
    const spender = capability.spender;
    const permit2 = capability.permit2Address;

    const [allowedAmount, , currentNonce] = await client.readContract({
      address: permit2,
      abi: PERMIT2_ABI,
      functionName: "allowance",
      args: [owner, token, spender]
    });

    // Case A: Pending/unconsumed permit
    if (currentNonce === capability.details.nonce) {
      const newNonce = currentNonce + 1;
      const calldata = encodeFunctionData({
        abi: PERMIT2_ABI,
        functionName: "invalidateNonces",
        args: [token, spender, newNonce]
      });

      return {
        strategy: "INVALIDATE_NONCE",
        description: `Owner calls invalidateNonces(token, spender, ${newNonce}) advancing nonce to prevent permit consumption`,
        target: permit2,
        calldata,
        actor: owner
      };
    }

    // Case B: Consumed permit, allowance is active
    if (allowedAmount > 0n) {
      const calldata = encodeFunctionData({
        abi: PERMIT2_ABI,
        functionName: "lockdown",
        args: [[{ token, spender }]]
      });

      return {
        strategy: "LOCKDOWN_ALLOWANCE",
        description: `Owner calls lockdown([{ token, spender }]) to immediately zero out the compromised allowance`,
        target: permit2,
        calldata,
        actor: owner
      };
    }

    return {
      strategy: "NOOP",
      description: "No active risk or capability already neutralized",
      target: permit2,
      calldata: "0x",
      actor: owner
    };
  }
}
