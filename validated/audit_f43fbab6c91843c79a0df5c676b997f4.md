## Title
Deposit system transaction can revert entirely and be silently dropped when both the recipient and `failedDepositVault` transfers fail, permanently freezing a real Bitcoin deposit — analog of M-5 (unclassified revert wrongly treated as terminal cancellation) - ([File: crates/evm/src/evm/system_contracts/src/Bridge.sol], [File: crates/sequencer/src/runner.rs])

### Summary
The Sherlock M-5 bug class is: a legitimate/valid state (an outstanding order submitted before prices went negative) gets permanently discarded because a plain, unclassified `revert` is treated the same as a "this should be cancelled" signal, instead of being retried/handled specially. The Citrea analog is in the deposit path of `Bridge.sol::deposit()` together with the sequencer's system-transaction handling in `crates/sequencer/src/runner.rs::process_sys_txs`: if `Bridge.deposit()` reverts (which is possible even for a *real*, validly-proven Bitcoin deposit, when both the recipient transfer and the `failedDepositVault` fallback transfer fail), the sequencer's special-case handling for deposits treats *any* `EvmSystemTransactionNotSuccessful` failure identically — it reverts the working set and `continue`s, dropping the deposit system transaction from the L2 block, while the corresponding deposit entry is never removed from the `DepositDataMempool` unless the transaction succeeds. If the on-chain condition that causes `Bridge.deposit()` to revert is persistent (e.g. `failedDepositVault` is a contract that always reverts, or is later paused/self-destructed), the deposit will fail every time it's retried, and the cBTC that should be minted for a real, proven BTC deposit is never credited — breaking the binding `cBTC minted == BTC actually deposited on L1`.

### Finding Description
`Bridge.deposit()` performs strict validation (SPV inclusion, Schnorr signature, script parsing, txId replay protection) and only after `processedTxIds[txId] = true` attempts to pay out cBTC: [1](#0-0) 

If the direct transfer to `recipient` fails, the contract falls back to `failedDepositVault`, but if *that* call also fails, the whole function reverts via `require(success, "Failed to send to failed deposit vault")`. Because `processedTxIds[txId] = true` and `depositTxIds.push(txId)` happen earlier in the same function body, a full revert of `deposit()` rolls back those writes too — so the deposit is not "processed and lost", it is fully reverted as an atomic EVM transaction failure.

The sequencer's system-transaction runner treats this revert specially, but only in a coarse way — it does not distinguish "a Bitcoin deposit that genuinely occurred and needs another attempt/administrative remedy" from "a malformed/replayed system tx that should simply be skipped": [2](#0-1) 

Because `is_deposit` matches solely on `SystemEvent::BridgeDeposit(_)` and the catch-all condition is any `EvmSystemTransactionNotSuccessful`, every revert reason inside `Bridge.deposit()` — including the "Failed to send to failed deposit vault" case, which represents a real, verified Bitcoin deposit that simply cannot currently be paid out — is handled identically to a deposit tx that is spurious or already processed: the working set is reverted and the sequencer moves on, dropping the system transaction from the block.

Crucially, the `DepositDataMempool` only removes a deposit once it has been *successfully* included in a block: [3](#0-2) [4](#0-3) 

So a deposit that always reverts (because `failedDepositVault` is permanently broken, e.g. set to an address that always reverts on receiving native value, or later becomes a self-destructed/paused contract) will be re-fetched and re-attempted on every subsequent L2 block, and will revert every time, without any operator alert distinguishing "genuinely undeliverable, proven deposit" from routine EVM failures. The BTC has been locked/moved on L1 (the `moveTx` was already verified and included), but the corresponding cBTC is never credited on Citrea — an unbounded, silent loop that never converges to the correct binding.

### Impact Explanation
This breaks the core rollup binding: **cBTC minted == BTC actually deposited on Bitcoin**. A user's BTC is provably locked/moved (proven via `BitcoinLightClient` SPV inclusion and the N-of-N Schnorr signature check inside `deposit()`), yet the corresponding cBTC credit is permanently unattainable if `failedDepositVault` cannot receive funds (e.g., due to owner misconfiguration, `PausableUpgradeable`-triggered downstream failure, or the vault becoming a contract that reverts). This is a "funds permanently frozen" condition per the Critical impact category — the deposit can never complete through the canonical path, and there is no code path shown that retries with a different recipient, alerts an operator, or otherwise resolves the stuck deposit; the sequencer will keep re-attempting and reverting it indefinitely, functionally freezing that BTC forever from the perspective of the Citrea side.

### Likelihood Explanation
Likelihood is Medium-Low: it requires the `failedDepositVault` (settable only by the `owner`) to become unable to accept a transfer, which is a configuration/operational condition rather than something an unprivileged attacker can trigger on demand. However, this is not solely a "deployment ignoring documented configuration" issue — `failedDepositVault` is explicitly designed as the safety-net receiver for reverting recipients, and its own possible failure mode (it too can revert, be a paused contract, or run out of gas due to complex fallback logic) is a legitimate on-chain state that the protocol itself must handle, not an operator error. The sequencer code's blanket handling of `EvmSystemTransactionNotSuccessful` for all deposits, regardless of the specific revert reason, means the protocol has no distinct handling for "provably real deposit that is stuck" versus "spurious/duplicate deposit event" — exactly the M-5 pattern of collapsing a distinguishable failure mode into a single, silently-discarded outcome.

### Recommendation
- In `Bridge.sol::deposit()`, do not let the fallback-vault failure revert the entire deposit function; instead, emit a distinct event/error marker so operators are alerted a deposit is stuck rather than continuously and silently retried and dropped.
- In `crates/sequencer/src/runner.rs::process_sys_txs`, disambiguate deposit failure reasons (e.g., via a specific revert selector for "failed to send to failed deposit vault" versus other deposit tx failures) rather than treating any `EvmSystemTransactionNotSuccessful` for a `BridgeDeposit` event identically; genuinely stuck deposits should trigger alerting/manual remediation instead of infinite silent re-queueing.
- Consider making `failedDepositVault` calls use a pattern that cannot itself fail (e.g., a plain `payable` predeploy with no logic, or crediting an internal balance mapping instead of an external call) so the deposit-completion invariant cannot be blocked by any contract-level failure.

### Proof of Concept
1. Owner calls `setFailedDepositVault` to point at a contract `V` that reverts unconditionally on receiving value (or `V` later becomes such, e.g. via upgrade/pause of downstream logic it relies on). [5](#0-4) 
2. A user performs a real Bitcoin deposit; the N-of-N produces a valid `moveTx`, which is relayed and included by the sequencer into the deposit mempool, then fetched via `fetch_deposits`. [6](#0-5) 
3. The recipient contract (or a 7702-enabled EOA) also reverts on receiving cBTC (attacker-controlled or accidental), causing `deposit()` to attempt the vault fallback, which also reverts, causing the whole `deposit()` call to revert with `"Failed to send to failed deposit vault"`.
4. `process_sys_txs` catches `EvmSystemTransactionNotSuccessful`, matches `is_deposit == true`, reverts the working set, decrements nonce, and `continue`s — the deposit tx never lands in an L2 block. [7](#0-6) 
5. Since the deposit was never included successfully, `remove_deposits` is never called for it, so it remains in `accepted_deposit_txs` and is refetched every block, reverting every time — the BTC deposit that occurred on L1 is never credited as cBTC on Citrea, for as long as the vault/recipient failure condition persists.

### Citations

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L163-170)
```text
    /// @notice Sets the address of the failed deposit vault
    /// @param _failedDepositVault The address of the failed deposit vault
    function setFailedDepositVault(address _failedDepositVault) external onlyOwner {
        require(_failedDepositVault != address(0), "Invalid address");
        address oldVault = failedDepositVault;
        failedDepositVault = _failedDepositVault;
        emit FailedDepositVaultUpdated(oldVault, _failedDepositVault);
    }
```

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L230-241)
```text
        address recipient = extractRecipientAddress(script);

        (bool success, ) = recipient.call{value: depositAmount}("");
        if(!success) {
            // If the transfer fails, we send the funds to the failed deposit vault
            emit DepositTransferFailed(wtxId, txId, recipient, block.timestamp, depositTxIds.length - 1);
            (success, ) = failedDepositVault.call{value: depositAmount}("");
            require(success, "Failed to send to failed deposit vault");
        } else {
            emit Deposit(wtxId, txId, recipient, block.timestamp, depositTxIds.length - 1);
        }
    }
```

**File:** crates/sequencer/src/runner.rs (L1678-1699)
```rust
            if let Err(e) = self
                .stf
                .apply_l2_block_txs(l2_block_info, &txs, &mut working_set)
            {
                // If a deposit failed, revert back the working set and continue,
                // as deposits to non-EOA addresses can revert
                // Decrement nonce to be able to process other system and non-system transactions
                if matches!(
                    e,
                    StateTransitionError::ModuleCallError(
                        L2BlockModuleCallError::EvmSystemTransactionNotSuccessful
                    )
                ) && is_deposit
                {
                    warn!("Deposit transaction failed: {:?}", e);
                    *nonce = nonce.saturating_sub(1);
                    working_set_to_discard = working_set.revert().to_revertable();
                    // evm_nonce stays the same — next tx gets the correct nonce
                    continue;
                }
                return Err(anyhow!("Failed to apply system transaction: {e:?}"));
            }
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L40-69)
```rust
    pub fn make_deposit_tx_from_data(&mut self, deposit_tx_data: Deposit) -> TransactionRequest {
        TransactionRequest {
            from: Some(SYSTEM_SIGNER),
            to: Some(TxKind::Call(BridgeWrapper::address())),
            input: TransactionInput::new(BridgeWrapper::deposit(deposit_tx_data)),
            gas: Some(SYSTEM_TX_GAS_LIMIT),
            ..Default::default()
        }
    }

    /// Retrieves a limited number of deposit transactions from the mempool without removing them
    ///
    /// # Arguments
    /// * `limit_per_block` - Maximum number of deposits to return
    ///
    /// # Returns
    /// A vector of deposit transaction data, limited by the specified amount
    pub fn fetch_deposits(&mut self, limit_per_block: usize) -> Vec<Deposit> {
        let number_of_deposits = self.accepted_deposit_txs.len().min(limit_per_block);
        SM.deposit_data_mempool_txs
            .set(self.accepted_deposit_txs.len() as f64);
        let deposits: Vec<Deposit> = self
            .accepted_deposit_txs
            .iter()
            .take(number_of_deposits)
            .cloned()
            .collect();

        deposits
    }
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L71-109)
```rust
    /// Removes specific deposits from the mempool after they have been successfully included in a block
    ///
    /// # Arguments
    /// * `deposits_to_remove` - The deposits that were successfully included
    ///
    /// # Returns
    /// The number of deposits actually removed
    #[instrument(level = "trace", skip_all, ret)]
    pub fn remove_deposits(&mut self, deposits_to_remove: &[Deposit]) -> usize {
        let mut removed_count = 0;

        // Calculate txids for the deposits to remove
        let mut txids_to_remove = HashSet::new();
        for deposit in deposits_to_remove {
            let txid = Self::calc_tx_id(deposit)
                .expect("calc_tx_id should never be called on non-deposit");
            txids_to_remove.insert(txid.to_vec());
        }

        // Retain only deposits that are not in the removal set
        self.accepted_deposit_txs.retain(|deposit| {
            let txid = Self::calc_tx_id(deposit)
                .expect("calc_tx_id should never be called on non-deposit");
            if txids_to_remove.contains(txid.as_slice()) {
                // Remove from pending set
                self.pending_deposits.remove(txid.as_slice());
                removed_count += 1;
                return false;
            }
            true
        });

        // Update metrics
        SM.deposit_data_mempool_txs
            .set(self.accepted_deposit_txs.len() as f64);

        debug!("Removed {} deposits from mempool", removed_count);
        removed_count
    }
```
