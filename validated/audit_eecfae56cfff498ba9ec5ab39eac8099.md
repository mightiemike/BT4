[1](#0-0)

### Citations

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L28-38)
```text
    modifier onlySystem() {
        require(msg.sender == SYSTEM_CALLER, "caller is not the system caller");
        _;
    }

    /// @notice Sets the initial value for the block number, can only be called once
    /// @param _blockNumber L1 block number that is associated with the genesis block of Citrea
    function initializeBlockNumber(uint256 _blockNumber) external onlySystem {
        require(blockNumber == 0, "Already initialized");
        blockNumber = _blockNumber;
    }
```
