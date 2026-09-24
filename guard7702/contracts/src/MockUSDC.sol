pragma solidity 0.8.17;

import {ERC20} from "lib/permit2/lib/solmate/src/tokens/ERC20.sol";

contract MockUSDC is ERC20 {
    constructor() ERC20("USD Coin", "USDC", 6) {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
