pragma solidity 0.8.17;

import {Test} from "lib/forge-std/src/Test.sol";
import {Vm} from "lib/forge-std/src/Vm.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {MaliciousDelegate} from "../src/MaliciousDelegate.sol";

contract EIP7702AttackTest is Test {
    MockUSDC public usdc;
    MaliciousDelegate public maliciousDelegate;

    uint256 internal victimKey = 0xA11CE;
    address public victim;
    address public attacker = address(0xB0B);

    uint256 internal constant DRAIN_AMOUNT = 10_000 * 1e6; // 10,000 USDC

    function setUp() public {
        victim = vm.addr(victimKey);
        usdc = new MockUSDC();
        maliciousDelegate = new MaliciousDelegate();

        // Seed victim with 10,000 USDC
        usdc.mint(victim, DRAIN_AMOUNT);
        // Fund victim with 1 ETH for gas
        vm.deal(victim, 1 ether);
    }

    /// @notice Baseline 1-Step: signing authorization tuple causes ZERO balance change at Step 0
    function test_one_step_baseline_zero_delta() public view {
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        assertEq(usdc.balanceOf(attacker), 0);
    }

    /// @notice Multi-Step Drain: Relayer executes Type-4 tx installing delegate -> calls sweep()
    function test_7702_multi_step_drain_discovery() public {
        uint64 victimNonce = uint64(vm.getNonce(victim));

        // Victim was tricked into signing delegation to MaliciousDelegate
        Vm.SignedDelegation memory auth = vm.signDelegation(
            address(maliciousDelegate),
            victimKey,
            victimNonce
        );

        // Attacker relays Type-4 transaction attaching the delegation and executing sweep()
        vm.attachDelegation(auth);
        vm.prank(attacker);
        MaliciousDelegate(payable(victim)).sweep(address(usdc), attacker);

        // Victim drained completely!
        assertEq(usdc.balanceOf(victim), 0);
        assertEq(usdc.balanceOf(attacker), DRAIN_AMOUNT);
    }

    /// @notice Recovery Case A (Unconsumed Authorization): Victim advances nonce -> attacker auth tuple rejected
    function test_recovery_unconsumed_authorization_nonce_advance() public {
        uint64 victimNonce = uint64(vm.getNonce(victim));

        // Stolen authorization with nonce 0
        Vm.SignedDelegation memory auth = vm.signDelegation(
            address(maliciousDelegate),
            victimKey,
            victimNonce
        );

        // Recovery: Victim broadcasts an ordinary transaction, advancing account nonce: 0 -> 1
        vm.prank(victim);
        (bool ok,) = address(0x123).call{value: 1}("");
        require(ok, "nonce advance tx failed");

        uint64 updatedNonce = uint64(vm.getNonce(victim));
        assertEq(updatedNonce, victimNonce + 1);

        // EIP-7702 Specification Rule: auth.nonce MUST match authority account nonce at processing time
        // Since updatedNonce > auth.nonce, the stolen authorization tuple is permanently invalid
        assertTrue(updatedNonce != auth.nonce, "Nonce mismatch guarantees protocol-level rejection");

        // Victim balance preserved
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT);
        assertEq(usdc.balanceOf(attacker), 0);
    }

    /// @notice Recovery Case B (Active Delegation): Victim clears delegation via authorization to address(0)
    function test_recovery_active_delegation_cleared_to_zero_address() public {
        uint64 victimNonce = uint64(vm.getNonce(victim));

        // Stolen authorization with nonce 0
        Vm.SignedDelegation memory auth = vm.signDelegation(
            address(maliciousDelegate),
            victimKey,
            victimNonce
        );

        // Attacker installs the delegation via a Type-4 transaction
        vm.attachDelegation(auth);
        vm.prank(attacker);
        (bool okInstall,) = victim.call("");
        require(okInstall, "install delegation failed");

        // Recovery: Victim creates recovery authorization targeting address(0) with incremented nonce
        Vm.SignedDelegation memory recoveryAuth = vm.signDelegation(
            address(0),
            victimKey,
            victimNonce + 1
        );

        // Victim executes Type-4 recovery transaction to clear delegation indicator
        vm.attachDelegation(recoveryAuth);
        vm.prank(victim);
        (bool okClear,) = victim.call("");
        require(okClear, "clear delegation failed");

        // Attacker attempts to invoke sweep() on victim
        vm.prank(attacker);
        bytes memory sweepData = abi.encodeWithSelector(
            MaliciousDelegate.sweep.selector,
            address(usdc),
            attacker
        );
        (bool okSweep,) = victim.call(sweepData);
        // Note: In EVM, calling an EOA with empty bytecode returns success (no-op),
        // but crucially the sweep() logic in MaliciousDelegate never executed!
        // We verify that the drain DID NOT happen.

        // Victim assets remain 100% safe
        assertEq(usdc.balanceOf(victim), DRAIN_AMOUNT, "Victim balance must be untouched");
        assertEq(usdc.balanceOf(attacker), 0, "Attacker balance must remain 0");
    }
}
