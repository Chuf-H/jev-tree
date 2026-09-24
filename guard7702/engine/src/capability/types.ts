import { Address, Hex } from "viem";

export type CapabilityKind = "PERMIT2_ALLOWANCE" | "PERMIT2_SIGNATURE" | "EIP7702";

export interface BaseCapability {
  kind: CapabilityKind;
  owner: Address;
  chainId: bigint;
}

export interface Permit2AllowanceCapability extends BaseCapability {
  kind: "PERMIT2_ALLOWANCE";
  permit2Address: Address;
  details: {
    token: Address;
    amount: bigint;
    expiration: number;
    nonce: number;
  };
  spender: Address;
  sigDeadline: bigint;
  signature: Hex;
}

export interface Permit2SignatureCapability extends BaseCapability {
  kind: "PERMIT2_SIGNATURE";
  permit2Address: Address;
  permitted: {
    token: Address;
    amount: bigint;
  };
  nonce: bigint;
  deadline: bigint;
  spender: Address;
  signature: Hex;
}

export interface EIP7702Capability extends BaseCapability {
  kind: "EIP7702";
  delegateAddress: Address;
  nonce: bigint;
  yParity: number;
  r: Hex;
  s: Hex;
  targetToken?: Address;
  authorizationObject?: any;
}

export type Capability =
  | Permit2AllowanceCapability
  | Permit2SignatureCapability
  | EIP7702Capability;

export interface Action {
  id: string;
  description: string;
  target: Address;
  calldata: Hex;
  value: bigint;
  actor: Address;
  authorizationList?: any[];
}

export interface SearchNode {
  depth: number;
  snapshotId: Hex;
  trace: Action[];
  victimBalance: bigint;
}

export interface Counterexample {
  capability: CapabilityKind;
  owner: Address;
  attacker: Address;
  depth: number;
  trace: Action[];
  loss: {
    token: Address;
    symbol: string;
    amount: string;
    formatted: string;
  };
}
