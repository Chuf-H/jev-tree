import { encodeFunctionData, PublicClient, Hex, Address } from "viem";
import { Permit2SignatureCapability } from "../capability/types.js";
import { PERMIT2_ABI } from "../capability/abis.js";

export interface SignatureRecoveryAction {
  strategy: "INVALIDATE_UNORDERED_NONCE" | "NOOP";
  description: string;
  target: Address;
  calldata: Hex;
  actor: Address; // Must be owner because Permit2 checks msg.sender
  wordPos: bigint;
  mask: bigint;
}

export class Permit2SignatureRecoveryPlanner {
  static async plan(
    capability: Permit2SignatureCapability,
    client: PublicClient
  ): Promise<SignatureRecoveryAction> {
    const owner = capability.owner;
    const permit2 = capability.permit2Address;
    const nonce = capability.nonce;

    const wordPos = nonce >> 8n;
    const bitPos = Number(nonce & 255n);
    const mask = 1n << BigInt(bitPos);

    const currentWord: bigint = await client.readContract({
      address: permit2,
      abi: PERMIT2_ABI,
      functionName: "nonceBitmap",
      args: [owner, wordPos]
    });

    if ((currentWord & mask) === 0n) {
      const calldata = encodeFunctionData({
        abi: PERMIT2_ABI,
        functionName: "invalidateUnorderedNonces",
        args: [wordPos, mask]
      });

      return {
        strategy: "INVALIDATE_UNORDERED_NONCE",
        description: `Owner calls invalidateUnorderedNonces(wordPos=${wordPos}, mask=${mask}) setting bit ${bitPos} to permanently revoke nonce ${nonce}`,
        target: permit2,
        calldata,
        actor: owner,
        wordPos,
        mask
      };
    }

    return {
      strategy: "NOOP",
      description: `Nonce ${nonce} already spent or invalidated on-chain`,
      target: permit2,
      calldata: "0x",
      actor: owner,
      wordPos,
      mask
    };
  }
}
