No vulnerability found for this question.

The reported bug class—a `claimable()`-style function that recomputes a per-user allocation (e.g., `initialRelease`) without subtracting what has already been claimed, letting repeated calls drain funds—does not have an analog in this repository's in-scope contracts.

- `Bridge.sol`'s `deposit`/`replaceDeposit` prevent replay via `processedTxIds[txId]`, a one-time-set mapping checked before any value transfer, so repeated calls with the same proof cannot re-credit cBTC. [1](#0-0) [2](#0-1) 
- `withdraw`/`batchWithdraw`/`safeWithdraw` prevent double-spending via `usedWithdrawalUTXO[utxoKey]`, also set before the UTXO is recorded, so a user cannot repeatedly "claim" a withdrawal against the same UTXO. [3](#0-2) 
- `FeeVault.withdraw()` simply transfers the contract's *current* balance to a fixed `recipient` and does not track a per-user cumulative "claimed" amount that could be miscalculated on repeat calls; each call moves the entire remaining balance, so there is nothing left to re-drain. [4](#0-3) 

None of the in-scope Solidity contracts implement a vesting/allocation schedule with a "claimed so far" bookkeeping variable that could be bypassed the way `TrufVesting.claimable` was, and no other in-scope binding (deposit-vs-Bitcoin, journal-vs-actual, commitment/method-id upgrade, or native-vs-guest root) is affected by this bug class.

### Citations

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L211-213)
```text
        require(!processedTxIds[txId], "txId already spent");
        processedTxIds[txId] = true;
        depositTxIds.push(txId);
```

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L246-260)
```text
    function withdraw(bytes32 txId, bytes4 outputId) public payable whenNotPaused {
        require(msg.value == depositAmount, "Invalid withdraw amount");

        bytes32 utxoKey = sha256(abi.encodePacked(txId, outputId));
        require(!usedWithdrawalUTXO[utxoKey], "UTXO already used");
        usedWithdrawalUTXO[utxoKey] = true;

        UTXO memory utxo = UTXO({
            txId: txId,
            outputId: outputId
        });
        uint256 index = withdrawalUTXOs.length;
        withdrawalUTXOs.push(utxo);
        emit Withdrawal(utxo, index, block.timestamp);
    }
```

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L386-389)
```text
        // Nullify the replace transaction based on txId
        bytes32 newTxId = ValidateSPV.calculateTxId(replaceTx.version, replaceTx.vin, replaceTx.vout, replaceTx.locktime);
        require(!processedTxIds[newTxId], "txId already used to replace");
        processedTxIds[newTxId] = true;
```

**File:** crates/evm/src/evm/system_contracts/src/FeeVault.sol (L24-33)
```text
    /// @notice Withdraws accumulated fees to recipient if enough funds are accumulated
    function withdraw() external {
        address _recipient = recipient;
        require(_recipient != address(0), "Recipient is not set");
        uint256 amount = address(this).balance;
        require(amount >= minWithdraw, "Withdrawal amount must be greater than minimum withdraw amount");
        (bool success, ) = payable(_recipient).call{value: amount}("");
        require(success, "Transfer failed");
        emit Withdrawal(_recipient, amount);
    }
```
