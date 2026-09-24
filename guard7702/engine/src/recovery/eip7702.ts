import { PublicClient, Address, Hex } from "viem";
import { EIP7702Capability } from "../capability/types.js";

export interface EIP7702RecoveryAction {
  strategy: "ADVANCE_NONCE" | "CLEAR_DELEGATION" | "NOOP";
  description: string;
  target: Address;
  calldata: Hex;
  actor: Address;
  recoveryDelegation?: {
    address: Address;
    chainId: number;
    nonce: number;
  };
}

export class EIP7702RecoveryPlanner {
  static async plan(
    capability: EIP7702Capability,
    client: PublicClient
  ): Promise<EIP7702RecoveryAction> {
    const owner = capability.owner;
    const currentNonce = await client.getTransactionCount({ address: owner });
    const currentBytecode = await client.getBytecode({ address: owner });

    const hasDelegation =
      currentBytecode &&
      currentBytecode.length >= 48 &&
      currentBytecode.toLowerCase().startsWith("0xef0100");

    // Case 1: Active delegation on victim EOA -> Clear delegation by setting address(0)
    if (hasDelegation) {
      return {
        strategy: "CLEAR_DELEGATION",
        description: `Owner broadcasts Type-4 recovery transaction with authorization for address(0) to reset code to standard EOA`,
        target: owner,
        calldata: "0x",
        actor: owner,
        recoveryDelegation: {
          address: "0x0000000000000000000000000000000000000000",
          chainId: Number(capability.chainId),
          nonce: currentNonce
        }
      };
    }

    // Case 2: Unconsumed authorization -> Advance account nonce
    if (BigInt(currentNonce) <= capability.nonce) {
      return {
        strategy: "ADVANCE_NONCE",
        description: `Owner executes self-transaction to increment account nonce from ${currentNonce} to ${currentNonce + 1}, invalidating stolen authorization`,
        target: owner,
        calldata: "0x",
        actor: owner
      };
    }

    return {
      strategy: "NOOP",
      description: "Stolen authorization already invalidated by higher account nonce; no active delegation detected",
      target: owner,
      calldata: "0x",
      actor: owner
    };
  }
}
