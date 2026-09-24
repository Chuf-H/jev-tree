// SPDX-License-Identifier: MIT
pragma solidity 0.8.17;

import "forge-std/Test.sol";
import "../src/Guard7702Sentinel.sol";
import "../src/MockUSDC.sol";
import "permit2/src/Permit2.sol";

contract Guard7702SentinelTest is Test {
    Guard7702Sentinel public sentinel;
    Permit2 public permit2;
    MockUSDC public usdc;

    uint256 internal ownerKey = 0xAAAA;
    address public owner;
    address public spender = address(0xBBBB);

    function setUp() public {
        owner = vm.addr(ownerKey);
        sentinel = new Guard7702Sentinel();
        permit2 = new Permit2();
        usdc = new MockUSDC();
        vm.deal(owner, 1 ether);
    }

    function test_batch_invalidate_nonces() public {
        address[] memory tokens = new address[](1);
        tokens[0] = address(usdc);

        address[] memory spenders = new address[](1);
        spenders[0] = spender;

        uint48[] memory newNonces = new uint48[](1);
        newNonces[0] = 5;

        uint64 ownerNonce = uint64(vm.getNonce(owner));
        Vm.SignedDelegation memory auth = vm.signDelegation(
            address(sentinel),
            ownerKey,
            ownerNonce
        );
        vm.attachDelegation(auth);

        vm.prank(owner);
        Guard7702Sentinel(payable(owner)).batchInvalidateNonces(address(permit2), tokens, spenders, newNonces);

        (,, uint48 onChainNonce) = permit2.allowance(owner, address(usdc), spender);
        assertEq(onChainNonce, 5, "Nonce must be updated to 5 on Permit2");
    }

    function test_batch_invalidate_unordered_nonces() public {
        uint256[] memory wordPositions = new uint256[](1);
        wordPositions[0] = 4;

        uint256[] memory masks = new uint256[](1);
        masks[0] = 2; // bit 1

        uint64 ownerNonce = uint64(vm.getNonce(owner));
        Vm.SignedDelegation memory auth = vm.signDelegation(
            address(sentinel),
            ownerKey,
            ownerNonce
        );
        vm.attachDelegation(auth);

        vm.prank(owner);
        Guard7702Sentinel(payable(owner)).batchInvalidateUnorderedNonces(address(permit2), wordPositions, masks);

        uint256 bitmap = permit2.nonceBitmap(owner, 4);
        assertEq(bitmap, 2, "Bitmap bit 1 must be set to 1");
    }
}
