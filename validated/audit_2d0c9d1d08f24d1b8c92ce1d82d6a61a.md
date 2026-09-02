### Title
`remove_deposits` unconditionally purges the full fetched deposit batch even when `process_sys_txs` reverted-and-skipped a deposit, permanently orphaning a still-locked Bitcoin deposit - ([File: crates/sequencer/src/runner.rs])

### Summary
`produce_l2_block_inner` fetches a batch of pending deposits with `fetch_deposits`, feeds it into `produce_and_run_system_transactions` → `process_sys_txs`, and after block production calls `self.deposit_mempool.lock().remove_deposits(&deposit_data)` using the *original, unfiltered* `deposit_data` vector rather than the subset that `process_sys_txs` actually applied successfully. Since `process_sys_txs` explicitly reverts and `continue`s on `EvmSystemTransactionNotSuccessful` for a `BridgeDeposit` event without excising that item from `deposit_data`, any deposit whose on-chain application fails at block-build time is removed from `DepositDataMempool` even though `BridgeContract.deposit()` never executed and no cBTC was minted.

### Finding Description
The binding that must hold is:
`removed_set (deposit_mempool.remove_deposits argument)` == `applied_set (deposits whose BridgeContract.deposit() call actually succeeded in the produced block)`.

Trace:
- `produce_l2_block_inner` fetches deposits: `let deposit_data = self.deposit_mempool.lock().fetch_deposits(...)` [1](#0-0) .
- This same `deposit_data` is passed down to `produce_and_run_system_transactions`, which turns it into `SystemEvent::BridgeDeposit` events and calls `process_sys_txs` [2](#0-1) .
- Inside `process_sys_txs`, on `StateTransitionError::ModuleCallError(L2BlockModuleCallError::EvmSystemTransactionNotSuccessful)` for `is_deposit = matches!(event, SystemEvent::BridgeDeposit(_))`, the working set is reverted, the nonce decremented, and the loop simply `continue`s — the failed event is dropped from `all_txs` (the successfully-applied list) but nothing propagates this failure back to the caller's `deposit_data` vector [3](#0-2) .
- Later, after the block is saved, `produce_l2_block_inner` removes deposits from the mempool using the *original* `deposit_data`, not the filtered `all_txs`/success set: `self.deposit_mempool.lock().remove_deposits(&deposit_data)` [4](#0-3) .
- `DepositDataMempool::remove_deposits` deletes every deposit whose txid matches one in the passed slice, with no on-chain success check performed — it trusts the caller entirely [5](#0-4) .
- The `EvmSystemTransactionNotSuccessful` failure mode is real and reachable in production: the executor returns this error whenever a system tx's `result_and_state.result` is not successful [6](#0-5) , and the sequencer's own tests demonstrate that a second/duplicate or malformed deposit call reverts this way [7](#0-6) . The narrower question of whether an attacker can force this specific failure via a self-deployed reentrant-fallback recipient and a same-block ordinary tx that flips storage could not be fully confirmed: `Bridge.sol` appears to route failed value transfers to a `failedDepositVault` rather than reverting the whole `deposit()` call [8](#0-7) , which may blunt the specific reentrancy-timing griefing vector described. However, the underlying mempool/on-chain-state desync is independent of that specific griefing mechanism — *any* cause of `EvmSystemTransactionNotSuccessful` for a `BridgeDeposit` event (gas-limit interaction, other require() failures inside `deposit()`, malformed-but-otherwise-real Merkle proof timing, etc.) triggers the same unconditional removal.

The RPC-time `eth_call` simulation at `BlockId::pending()` in `send_raw_deposit_transaction` only proves the deposit *would* succeed against pending state at submission time [9](#0-8) ; it provides no guarantee about state at actual block-build time after ordinary txs from the same or other senders are applied first, so a TOCTOU gap genuinely exists between acceptance and inclusion.

### Impact Explanation
If a deposit is fetched into a block, fails during `process_sys_txs`, and is nonetheless present in `deposit_data` passed to `remove_deposits`, it is permanently deleted from `DepositDataMempool.pending_deposits`/`accepted_deposit_txs` while zero cBTC was minted on Citrea and the depositor's BTC remains locked in the bridge multisig. Because the deposit's txid is expunged from `pending_deposits`, the depositor could in principle resubmit via `citrea_sendRawDepositTransaction`, but only if they realize the deposit silently vanished — from the depositor's perspective, this is a fund-availability defect that requires manual resubmission, and if resubmission again lands in a block where it fails (e.g. because the same underlying non-attacker-related condition recurs), the BTC stays permanently un-mintable with no automatic protocol-level retry. This is repeatable on every affected block and does not require any privileged role, matching the "funds permanently frozen" Critical impact category.

### Likelihood Explanation
The precondition is simply that a `BridgeDeposit` system event fails inside `process_sys_txs` for any reason after having passed the sequencer's earlier `eth_call` gate — a state that the code base already demonstrates is reachable (duplicate deposit, malformed input count in the existing unit tests). The specific attacker-engineered reentrancy/state-flip vector via a self-deployed contract and a colluding ordinary transaction in the same block is plausible in principle given the TOCTOU gap between `BlockId::pending()` simulation and actual block-build execution, but I could not fully confirm that `Bridge.sol`'s `deposit()` propagates a revert from a failing recipient transfer rather than diverting to `failedDepositVault` — the file was only partially visible. Regardless of that specific mechanism, the core mempool/state desync bug (`remove_deposits(&deposit_data)` using the unfiltered fetch result) is unconditionally present in `crates/sequencer/src/runner.rs` and requires no special privilege to trigger via any legitimate cause of a reverted `BridgeDeposit` system tx.

### Recommendation
Track which deposits from `deposit_data` were actually applied successfully inside `process_sys_txs` (e.g., return the successfully-applied subset alongside `all_txs`, or collect the failed deposit's raw bytes on the `continue` branch) and pass only that successful subset to `self.deposit_mempool.lock().remove_deposits(...)` in `produce_l2_block_inner`, leaving reverted deposits in the mempool for automatic retry in a subsequent block.

### Proof of Concept
```rust
// crates/sequencer/src/runner.rs (or a new integration test module)
// 1. Seed DepositDataMempool with a well-formed deposit D whose recipient is a
//    deployed contract that reverts on `receive`/fallback when a storage flag is set.
// 2. Submit an ordinary EVM tx (from the same or another account) earlier in the
//    mempool that flips that storage flag, and ensure it is ordered before the
//    deposit's system tx in dry-run/ordering.
// 3. Drive produce_l2_block_inner (or directly process_sys_txs) with:
//      deposit_data = vec![D.clone()]
//    and assert that:
//      a) process_sys_txs returns all_txs NOT containing D's encoded system tx
//         (i.e., the deposit failed with EvmSystemTransactionNotSuccessful and hit `continue`)
//      b) BridgeContract's on-chain state shows deposit() was never successfully executed
//         for D's txid (no balance credited to the recipient, no Deposit event emitted)
//      c) After block production, deposit_mempool.remove_deposits(&deposit_data) is called
//         with `deposit_data` still containing D
//      d) assert!(!deposit_mempool.pending_deposits.contains(D_txid))
//         while assert_eq!(recipient_balance, U256::ZERO) -- proving the deposit was purged
//         from the mempool despite never minting cBTC.
```

### Citations

**File:** crates/sequencer/src/runner.rs (L516-520)
```rust
        // Get pending deposits up to configured limit
        let deposit_data = self
            .deposit_mempool
            .lock()
            .fetch_deposits(self.config.deposit_mempool_fetch_limit);
```

**File:** crates/sequencer/src/runner.rs (L636-643)
```rust
        // Remove successfully included deposits from the mempool
        if !deposit_data.is_empty() {
            let removed_count = self.deposit_mempool.lock().remove_deposits(&deposit_data);
            debug!(
                "Removed {} deposits from mempool after successful block production",
                removed_count
            );
        }
```

**File:** crates/sequencer/src/runner.rs (L1596-1606)
```rust
        let deposit_events = populate_deposit_system_events(deposit_data);

        system_events.extend(deposit_events);

        self.process_sys_txs(
            l2_block_info,
            working_set_to_discard,
            nonce,
            evm,
            system_events,
        )
```

**File:** crates/sequencer/src/runner.rs (L1678-1703)
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
            evm_nonce += 1; // only increment on success
            working_set_to_discard = working_set.checkpoint().to_revertable();
            all_txs.push(sys_tx_rlp);
        }
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L79-101)
```rust
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
```

**File:** crates/evm/src/evm/executor.rs (L127-133)
```rust
        if !*should_be_end_of_sys_txs && !result_and_state.result.is_success() {
            native_error!(
                "System transaction not successful. Result: {:?}",
                result_and_state.result
            );
            return Err(L2BlockModuleCallError::EvmSystemTransactionNotSuccessful);
        }
```

**File:** crates/evm/src/tests/sys_tx_tests.rs (L812-840)
```rust
    // call deposit 2nd time with the exact same deposit data should fail
    evm.begin_l2_block_hook(&l2_block_info, &mut working_set);
    {
        let deposit_data = deposit_data.clone();
        let txs = vec![deposit_system_tx(deposit_data, &evm, &mut working_set)];
        assert!(matches!(
            evm.call(CallMessage { txs }, &context, &mut working_set),
            Err(L2BlockModuleCallError::EvmSystemTransactionNotSuccessful),
        ));
    }
    evm.end_l2_block_hook(&l2_block_info, &mut working_set);
    evm.finalize_hook(&[99u8; 32], &mut working_set.accessory_state());

    l2_block_info.l2_height += 1;

    // call deposit with 2 inputs should fail
    evm.begin_l2_block_hook(&l2_block_info, &mut working_set);
    {
        // malform the input number from 2 as expected number of inputs is 1
        let mut deposit_data = deposit_data.clone();

        deposit_data[0] = 2;

        let txs = vec![deposit_system_tx(deposit_data, &evm, &mut working_set)];
        assert!(matches!(
            evm.call(CallMessage { txs }, &context, &mut working_set),
            Err(L2BlockModuleCallError::EvmSystemTransactionNotSuccessful),
        ));
    }
```

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

**File:** crates/sequencer/src/rpc.rs (L336-353)
```rust
        let evm = Evm::<DefaultContext>::default();
        let mut working_set = WorkingSet::new(self.context.storage.clone());

        let dep_tx = self
            .context
            .deposit_mempool
            .lock()
            .make_deposit_tx_from_data(deposit.clone().into());

        let start = std::time::Instant::now();
        let tx_res = evm.get_call(
            dep_tx,
            Some(BlockId::pending()),
            None,
            None,
            &mut working_set,
            &self.context.ledger,
        );
```
