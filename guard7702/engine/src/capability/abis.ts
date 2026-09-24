import { parseAbi } from "viem";

export const PERMIT2_ABI = parseAbi([
  "function permit(address owner, ((address token, uint160 amount, uint48 expiration, uint48 nonce) details, address spender, uint256 sigDeadline) permitSingle, bytes signature) external",
  "function transferFrom(address from, address to, uint160 amount, address token) external",
  "function allowance(address owner, address token, address spender) external view returns (uint160 amount, uint48 expiration, uint48 nonce)",
  "function invalidateNonces(address token, address spender, uint48 newNonce) external",
  "function lockdown((address token, address spender)[] approvals) external",
  "function nonceBitmap(address owner, uint256 wordPos) external view returns (uint256)",
  "function invalidateUnorderedNonces(uint256 wordPos, uint256 mask) external",
  "function permitTransferFrom(((address token, uint256 amount) permitted, uint256 nonce, uint256 deadline) permit, (address to, uint256 requestedAmount) transferDetails, address owner, bytes signature) external"
]);

export const ERC20_ABI = parseAbi([
  "function balanceOf(address account) external view returns (uint256)",
  "function allowance(address owner, address spender) external view returns (uint256)",
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function symbol() external view returns (string)",
  "function decimals() external view returns (uint8)"
]);

export const MALICIOUS_DELEGATE_ABI = parseAbi([
  "function sweep(address token, address recipient) external",
  "function execute(address target, uint256 value, bytes data) external payable returns (bytes)"
]);
