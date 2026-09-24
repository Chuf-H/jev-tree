pragma solidity 0.8.17;

import {Test} from "lib/forge-std/src/Test.sol";
import {Permit2} from "lib/permit2/src/Permit2.sol";
import {IAllowanceTransfer} from "lib/permit2/src/interfaces/IAllowanceTransfer.sol";
import {InvalidNonce} from "lib/permit2/src/PermitErrors.sol";
import {PermitHash} from "lib/permit2/src/libraries/PermitHash.sol";
import {MockUSDC} from "../src/MockUSDC.sol";

contract Permit2AllowanceTest is Test {
    using PermitHash for IAllowanceTransfer.PermitSingle;

    Permit2 public permit2;
    MockUSDC public usdc;

    uint256 internal victimKey = 0xA11CE;
    address public victim;
    address public attacker = address(0xB0B);

    uint160 internal constant DRAIN_AMOUNT = 10_000 * 1e6; // 10,000 USDC

    function setUp() public {
        victim = vm.addr(victimKey);
        permit2 = new Permit2();
        usdc = new MockUSDC();

        // Seed victim with 10,000 USDC
        usdc.mint(victim, DRAIN_AMOUNT);

        // Victim gives universal approval to Permit2
        vm.prank(victim);
        usdc.approve(address(permit2), type(uint256).max);
    }

    function _signPermitSingle(
        uint160 amount,
        uint48 expiration,
        uint48 nonce,
        uint256 sigDeadline
    ) internal view returns (IAllowanceTransfer.PermitSingle memory, bytes memory) {
        IAllowanceTransfer.PermitDetails memory details = IAllowanceTransfer.PermitDetails({
            token: address(usdc),
            amount: amount,
            expiration: expiration,
            nonce: nonce
        });

        IAllowanceTransfer.PermitSingle memory permitSingle = IAllowanceTransfer.PermitSingle({
            details: details,
            spender: attacker,
            sigDeadline: sigDeadline
        });

        bytes32 msgHash = keccak256(
            abi.encodePacked(
                "\x19\x01",
                permit2.DOMAIN_SEPARATOR(),
                permitSingle.hash()
            )
        );

        (uint8 v, bytes32 r, bytes32 s) = vm.sign(victimKey, msgHash);
        bytes memory sig = abi.encodePacked(r, s, v);

        return (permitSingle, sig);
    }

    /// @notice Baseline 1-Step: signing capability results in ZERO balance delta at Step 0
    function test_one_step_baseline_zero_delta() public view {
        // Signing the off-chain PermitSingle does not change on-chain state
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        assertEq(usdc.balanceOf(attacker), 0);

        (uint160 amount,,) = permit2.allowance(victim, address(usdc), attacker);
        assertEq(amount, 0);
    }

    /// @notice Multi-Step Attack: permit() followed by transferFrom() executes 100% loss
    function test_multi_step_drain_discovery() public {
        (IAllowanceTransfer.PermitSingle memory permitSingle, bytes memory sig) = _signPermitSingle({
            amount: DRAIN_AMOUNT,
            expiration: uint48(block.timestamp + 1 days),
            nonce: 0,
            sigDeadline: block.timestamp + 1 hours
        });

        // Step 1: Attacker relays permit()
        vm.prank(attacker);
        permit2.permit(victim, permitSingle, sig);

        // At Step 1, victim balance has NOT changed yet, but future authority is granted!
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        (uint160 allowedAmount,,) = permit2.allowance(victim, address(usdc), attacker);
        assertEq(allowedAmount, DRAIN_AMOUNT);

        // Step 2: Attacker drains via transferFrom()
        vm.prank(attacker);
        permit2.transferFrom(victim, attacker, DRAIN_AMOUNT, address(usdc));

        // Victim drained!
        assertEq(usdc.balanceOf(victim), 0);
        assertEq(usdc.balanceOf(attacker), DRAIN_AMOUNT);
    }

    /// @notice Recovery Case A (Unconsumed permit): victim advances nonce -> attacker permit() reverts
    function test_recovery_unconsumed_permit_invalidates_nonce() public {
        (IAllowanceTransfer.PermitSingle memory permitSingle, bytes memory sig) = _signPermitSingle({
            amount: DRAIN_AMOUNT,
            expiration: uint48(block.timestamp + 1 days),
            nonce: 0,
            sigDeadline: block.timestamp + 1 hours
        });

        // Recovery: victim invalidates nonce (advancing from 0 to 1)
        vm.prank(victim);
        permit2.invalidateNonces(address(usdc), attacker, 1);

        // Attacker attempts Step 1: permit() -> MUST REVERT with InvalidNonce
        vm.prank(attacker);
        vm.expectRevert(InvalidNonce.selector);
        permit2.permit(victim, permitSingle, sig);

        // Victim assets remain 100% safe
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        assertEq(usdc.balanceOf(attacker), 0);
    }

    /// @notice Recovery Case B (Consumed permit, active allowance): victim calls lockdown() -> transferFrom reverts
    function test_recovery_consumed_permit_lockdown() public {
        (IAllowanceTransfer.PermitSingle memory permitSingle, bytes memory sig) = _signPermitSingle({
            amount: DRAIN_AMOUNT,
            expiration: uint48(block.timestamp + 1 days),
            nonce: 0,
            sigDeadline: block.timestamp + 1 hours
        });

        // Step 1: Attacker consumed permit()
        vm.prank(attacker);
        permit2.permit(victim, permitSingle, sig);

        // Recovery: Victim calls lockdown to revoke active allowance
        IAllowanceTransfer.TokenSpenderPair[] memory pairs = new IAllowanceTransfer.TokenSpenderPair[](1);
        pairs[0] = IAllowanceTransfer.TokenSpenderPair({token: address(usdc), spender: attacker});

        vm.prank(victim);
        permit2.lockdown(pairs);

        // Attacker attempts Step 2: transferFrom() -> MUST REVERT with InsufficientAllowance(0)
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(IAllowanceTransfer.InsufficientAllowance.selector, uint256(0)));
        permit2.transferFrom(victim, attacker, DRAIN_AMOUNT, address(usdc));

        // Victim assets remain 100% safe
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        assertEq(usdc.balanceOf(attacker), 0);
    }
}
