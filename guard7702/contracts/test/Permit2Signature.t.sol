pragma solidity 0.8.17;

import {Test} from "lib/forge-std/src/Test.sol";
import {Permit2} from "lib/permit2/src/Permit2.sol";
import {ISignatureTransfer} from "lib/permit2/src/interfaces/ISignatureTransfer.sol";
import {InvalidNonce} from "lib/permit2/src/PermitErrors.sol";
import {PermitHash} from "lib/permit2/src/libraries/PermitHash.sol";
import {MockUSDC} from "../src/MockUSDC.sol";

contract Permit2SignatureTest is Test {
    using PermitHash for ISignatureTransfer.PermitTransferFrom;

    Permit2 public permit2;
    MockUSDC public usdc;

    uint256 internal victimKey = 0xA11CE;
    address public victim;
    address public attacker = address(0xB0B);

    uint256 internal constant DRAIN_AMOUNT = 10_000 * 1e6; // 10,000 USDC
    uint256 internal constant NONCE = 1025; // wordPos = 4, bitPos = 1

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

    function _signPermitTransferFrom(
        uint256 amount,
        uint256 nonce,
        uint256 deadline
    ) internal view returns (ISignatureTransfer.PermitTransferFrom memory, bytes memory) {
        ISignatureTransfer.PermitTransferFrom memory permit = ISignatureTransfer.PermitTransferFrom({
            permitted: ISignatureTransfer.TokenPermissions({
                token: address(usdc),
                amount: amount
            }),
            nonce: nonce,
            deadline: deadline
        });

        bytes32 tokenPermissionsHash = keccak256(
            abi.encode(
                keccak256("TokenPermissions(address token,uint256 amount)"),
                permit.permitted.token,
                permit.permitted.amount
            )
        );

        bytes32 msgHash = keccak256(
            abi.encodePacked(
                "\x19\x01",
                permit2.DOMAIN_SEPARATOR(),
                keccak256(
                    abi.encode(
                        keccak256("PermitTransferFrom(TokenPermissions permitted,address spender,uint256 nonce,uint256 deadline)TokenPermissions(address token,uint256 amount)"),
                        tokenPermissionsHash,
                        attacker,
                        permit.nonce,
                        permit.deadline
                    )
                )
            )
        );

        (uint8 v, bytes32 r, bytes32 s) = vm.sign(victimKey, msgHash);
        bytes memory sig = abi.encodePacked(r, s, v);

        return (permit, sig);
    }

    /// @notice Baseline 1-Step: signing capability results in ZERO balance delta at Step 0
    function test_one_step_baseline_zero_delta() public view {
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        assertEq(usdc.balanceOf(attacker), 0);
    }

    /// @notice Direct Capability Drain: permitTransferFrom executes single-call drain of permitted amount
    function test_signature_transfer_drain() public {
        (ISignatureTransfer.PermitTransferFrom memory permit, bytes memory sig) = _signPermitTransferFrom({
            amount: DRAIN_AMOUNT,
            nonce: NONCE,
            deadline: block.timestamp + 1 hours
        });

        ISignatureTransfer.SignatureTransferDetails memory transferDetails = ISignatureTransfer.SignatureTransferDetails({
            to: attacker,
            requestedAmount: DRAIN_AMOUNT
        });

        // Attacker relays permitTransferFrom()
        vm.prank(attacker);
        permit2.permitTransferFrom(permit, transferDetails, victim, sig);

        // Victim drained!
        assertEq(usdc.balanceOf(victim), 0);
        assertEq(usdc.balanceOf(attacker), DRAIN_AMOUNT);
    }

    /// @notice Recovery: victim calls invalidateUnorderedNonces(wordPos, mask) -> attacker transaction reverts
    function test_recovery_invalidate_unordered_nonces() public {
        (ISignatureTransfer.PermitTransferFrom memory permit, bytes memory sig) = _signPermitTransferFrom({
            amount: DRAIN_AMOUNT,
            nonce: NONCE,
            deadline: block.timestamp + 1 hours
        });

        ISignatureTransfer.SignatureTransferDetails memory transferDetails = ISignatureTransfer.SignatureTransferDetails({
            to: attacker,
            requestedAmount: DRAIN_AMOUNT
        });

        // Recovery: calculate wordPos and bitPos
        uint256 wordPos = uint248(NONCE >> 8);
        uint256 bitPos = uint8(NONCE);
        uint256 mask = 1 << bitPos;

        // Victim invalidates the unordered nonce
        vm.prank(victim);
        permit2.invalidateUnorderedNonces(wordPos, mask);

        // Attacker attempts permitTransferFrom() -> MUST REVERT with InvalidNonce
        vm.prank(attacker);
        vm.expectRevert(InvalidNonce.selector);
        permit2.permitTransferFrom(permit, transferDetails, victim, sig);

        // Victim assets remain 100% safe
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        assertEq(usdc.balanceOf(attacker), 0);
    }
}
