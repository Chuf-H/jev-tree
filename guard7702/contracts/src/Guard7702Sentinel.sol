// SPDX-License-Identifier: MIT
pragma solidity 0.8.17;

interface IPermit2 {
    function invalidateNonces(address token, address spender, uint48 newNonce) external;
    function invalidateUnorderedNonces(uint256 wordPos, uint256 mask) external;
    struct TokenSpenderPair {
        address token;
        address spender;
    }
    function lockdown(TokenSpenderPair[] calldata approvals) external;
}

/**
 * @title Guard7702Sentinel
 * @notice On-chain atomic emergency sentinel for batch Permit2 invalidation
 *         and EIP-7702 delegation safety state management.
 */
contract Guard7702Sentinel {
    event NoncesInvalidated(address indexed owner, address indexed token, address indexed spender, uint48 newNonce);
    event UnorderedNoncesInvalidated(address indexed owner, uint256 indexed wordPos, uint256 mask);
    event EmergencyLockdownExecuted(address indexed owner, uint256 pairsCount);

    /// @notice Invalidate multiple sequential nonces on Permit2
    function batchInvalidateNonces(
        address permit2,
        address[] calldata tokens,
        address[] calldata spenders,
        uint48[] calldata newNonces
    ) external {
        require(tokens.length == spenders.length && spenders.length == newNonces.length, "Length mismatch");
        for (uint256 i = 0; i < tokens.length; i++) {
            IPermit2(permit2).invalidateNonces(tokens[i], spenders[i], newNonces[i]);
            emit NoncesInvalidated(msg.sender, tokens[i], spenders[i], newNonces[i]);
        }
    }

    /// @notice Invalidate multiple unordered bitmap nonces on Permit2
    function batchInvalidateUnorderedNonces(
        address permit2,
        uint256[] calldata wordPositions,
        uint256[] calldata masks
    ) external {
        require(wordPositions.length == masks.length, "Length mismatch");
        for (uint256 i = 0; i < wordPositions.length; i++) {
            IPermit2(permit2).invalidateUnorderedNonces(wordPositions[i], masks[i]);
            emit UnorderedNoncesInvalidated(msg.sender, wordPositions[i], masks[i]);
        }
    }

    /// @notice Emergency lockdown: atomic zeroing of active approvals on Permit2
    function emergencyLockdown(
        address permit2,
        IPermit2.TokenSpenderPair[] calldata approvals
    ) external {
        IPermit2(permit2).lockdown(approvals);
        emit EmergencyLockdownExecuted(msg.sender, approvals.length);
    }
}
