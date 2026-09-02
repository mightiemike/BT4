Confirmed. The mempool-removal call at `crates/sequencer/src/runner.rs:636-643` uses the raw `deposit_data` fetched at line 517-520, which is never filtered against the actual success/failure outcome computed in `process_sys_txs` (lines 1678-1702), where a failing `SystemEvent::BridgeDeposit` is reverted, its nonce rolled back, and its tx simply dropped (`continue`, no push to `all_txs`) without any signal being propagated back up to `produce_l2_block_inner`.

### Title
Deposit mempool removal is unconditional on the original fetch, not on actual on-chain deposit success - ([File: crates/sequencer/src/runner.rs])

### Summary
`produce_l2_block_inner` fetches a batch of deposits once via `fetch_deposits`, then internally the system-tx pipeline (`process_sys_txs`) may silently drop any individual `BridgeDeposit` whose `apply_l2_block_txs` returns `EvmSystemTransactionNotSuccessful` (revert + nonce rollback + `continue`), excluding it from the block's actual transactions. However, at the end of block production, `remove_deposits(&deposit_data)` is called with the *original, unfiltered* deposit list, so failed/reverted deposits are removed from the mempool exactly like successful ones.

### Finding Description
The invariant that should hold is: `deposit removed from DepositDataMempool == deposit's SystemEvent::BridgeDeposit was applied successfully (present in all_txs from process_sys_txs and credited cBTC)`.

Trace:
1. `deposit_data` is fetched once: [1](#0-0) .
2. It flows into `dry_run_transactions` → `produce_and_run_system_transactions` → `process_sys_txs`, where each `SystemEvent::BridgeDeposit` is applied on a checkpointed `WorkingSet`; on `EvmSystemTransactionNotSuccessful` the working set is reverted, the nonce decremented, and the loop `continue`s without adding the tx to `all_txs`: [2](#0-1) .
3. This filtered `all_txs`/`txs_to_run` result is what actually gets committed into the produced L2 block via `apply_l2_block_txs`: [3](#0-2) .
4. After the block is saved, the code removes deposits from the mempool using the *original* `deposit_data` variable from step 1 — not the subset that actually succeeded: [4](#0-3) .
5. `DepositDataMempool::remove_deposits` unconditionally deletes every matching `calc_tx_id` entry passed to it, with no success/failure distinction: [5](#0-4) .

Root cause: no channel exists from `process_sys_txs`'s per-deposit success/failure outcome back to the deposit-removal call site; the removal call re-uses the pre-fetch batch reference blindly, assuming "fetched this cycle" implies "applied this cycle."

The attacker path is exactly as posed in the question: `citrea_sendRawDepositTransaction` validates only via `evm.get_call` against `BlockId::pending()` at submission time: [6](#0-5) . `verify_system_tx`'s short-header-proof / `BitcoinLightClient` state check inside `execute_multiple_tx` is evaluated later, against whatever state exists when the deposit is actually pulled into a block, which can differ from the state at simulation time because intervening `SetBlockInfo` system events (or a duplicate-deposit condition) change the light-client/bridge state between simulation and inclusion: [7](#0-6) . The sequencer's own test suite demonstrates that a `BridgeDeposit` system tx does return `EvmSystemTransactionNotSuccessful` under such conditions (e.g., replay/duplicate or malformed state at apply time), and that this is treated as a per-deposit-skippable case rather than a batch-fatal error: [8](#0-7) .

Existing guards do not prevent this: `evm.get_call` simulation only proves validity at one point in time; there is no re-validation before removal, and `remove_deposits` has no concept of an "applied" vs "fetched" distinction.

### Impact Explanation
A deposit corresponding to a real, already-mined Bitcoin `MoveToVault` transaction can be permanently dropped from `DepositDataMempool` (`accepted_deposit_txs`/`pending_deposits`) without its `SystemEvent::BridgeDeposit` ever crediting cBTC. Since the entry is fully purged (not merely deferred), the depositor's expected cBTC credit is lost unless some external actor (e.g., Clementine's aggregator) independently notices the missing credit and resubmits the identical deposit blob via `citrea_sendRawDepositTransaction` — nothing in the sequencer automatically retries. This matches the Critical category "funds permanently frozen": a real Bitcoin-side move-to-vault output whose owed cBTC is never minted, with no on-chain guarantee of recovery. The blast radius is per-deposit and repeats every time the fetch/apply race condition recurs (any block where a batch of pending deposits happens to include one that reverts at apply time due to state drift since its `get_call` simulation).

### Likelihood Explanation
This does not require any privileged role — any party (the depositor, an aggregator, or an unrelated third party who has access to the public `MoveToVault` Bitcoin transaction data) can call `citrea_sendRawDepositTransaction`. The precondition (light-client/bridge state advancing between the RPC's `get_call` simulation at `BlockId::pending()` and the later real `process_sys_txs` execution) is a normal, expected occurrence in a running sequencer processing continuous L1 blocks and competing deposits, not a contrived edge case — it only requires timing where new `SetBlockInfo` events or another deposit affecting shared state land between simulation and inclusion. Attacker cost is limited to normal Bitcoin fees for the underlying deposit transaction (which must exist for the deposit blob to be well-formed) and is fully repeatable.

### Recommendation
Change `process_sys_txs`/`produce_and_run_system_transactions` to return the set of deposit entries (or their identifying `Deposit` bytes/txids) that were actually included in `all_txs`, and pass only that filtered/successful subset to `remove_deposits` in `produce_l2_block_inner`, instead of the original unfiltered `deposit_data` fetched from the mempool.

### Proof of Concept
```
cargo test -p citrea-sequencer deposit_removed_only_on_success -- --nocapture
```
Test plan:
1. Construct a `DepositDataMempool` with one valid deposit `D` and add it via `add_deposit_tx`.
2. Simulate the RPC-time state where `evm.get_call(BridgeWrapper::deposit(D), BlockId::pending())` succeeds (assert this using the same `Evm::get_call` path as `send_raw_deposit_transaction`).
3. Advance the underlying `WorkingSet`/light-client state (e.g., apply an extra `SetBlockInfo` `SystemEvent` or apply the same deposit once already so a second identical apply reverts) so that a subsequent real `process_sys_txs` run of the same `SystemEvent::BridgeDeposit(D)` returns `Err(StateTransitionError::ModuleCallError(L2BlockModuleCallError::EvmSystemTransactionNotSuccessful))`.
4. Call `process_sys_txs` and assert the returned `all_txs` does NOT contain `D`'s encoded system tx (i.e., `D` was skipped, no cBTC credited to recipient — assert recipient balance unchanged).
5. Call `deposit_mempool.remove_deposits(&[D])` exactly as `produce_l2_block_inner` does at line 638 (using the original unfiltered `deposit_data`), and assert it still returns `removed_count == 1`, proving the binding `removed == applied successfully` is violated.
6. Assert `deposit_mempool.pending_deposits` no longer contains `D`'s txid and that resubmitting via `add_deposit_tx(D)` succeeds as "new" — showing the deposit's original credit was silently lost with no retry mechanism.

### Citations

**File:** crates/sequencer/src/runner.rs (L516-520)
```rust
        // Get pending deposits up to configured limit
        let deposit_data = self
            .deposit_mempool
            .lock()
            .fetch_deposits(self.config.deposit_mempool_fetch_limit);
```

**File:** crates/sequencer/src/runner.rs (L573-580)
```rust
        let (signed_txs, blobs) = self.encode_and_sign_evm_txs_into_sov_txs(
            &mut working_set,
            &l2_block_info,
            txs_to_run.clone(),
        )?;

        self.instrumented_apply_l2_block_txs(&l2_block_info, &signed_txs, &mut working_set)?;
        self.instrumented_end_l2_block(l2_block_info, &mut working_set)?;
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

**File:** crates/sequencer/src/runner.rs (L1678-1702)
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
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L79-109)
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

        // Update metrics
        SM.deposit_data_mempool_txs
            .set(self.accepted_deposit_txs.len() as f64);

        debug!("Removed {} deposits from mempool", removed_count);
        removed_count
    }
```

**File:** crates/sequencer/src/rpc.rs (L330-359)
```rust
    fn send_raw_deposit_transaction(&self, deposit: Bytes) -> RpcResult<()> {
        debug!("Sequencer: citrea_sendRawDepositTransaction");

        let deposit_tx_size = deposit.len();
        SM.deposit_tx_size.record(deposit_tx_size as f64);

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
        let deposit_tx_call_duration = Instant::now()
            .saturating_duration_since(start)
            .as_secs_f64();
        SM.deposit_tx_call_duration.record(deposit_tx_call_duration);

        match tx_res {
```

**File:** crates/evm/src/evm/executor.rs (L97-133)
```rust
        if tx.signer() == SYSTEM_SIGNER {
            if *should_be_end_of_sys_txs {
                native_error!("System transaction found after user txs");
                return Err(L2BlockModuleCallError::EvmSystemTransactionPlacedAfterUserTx);
            }

            verify_system_tx(evm.evm.ctx().db(), tx, l2_height)?;
        } else {
            // Set to true as soon as a user tx is found
            // If a sys tx is encountered after a user tx it is an error
            *should_be_end_of_sys_txs = true;
        }

        // if tx is eip4844 error out
        if tx.is_eip4844() {
            native_error!("EIP-4844 transaction is not supported");
            return Err(L2BlockModuleCallError::EvmTxTypeNotSupported(
                "EIP-4844".to_string(),
            ));
        }

        let result_and_state = evm.transact(tx).map_err(|e| {
            native_error!("Invalid tx {}. Error: {}", tx.hash(), e);
            match e {
                // only custom error we use is for not enough funds for L1 fee
                EVMError::Custom(_) => L2BlockModuleCallError::EvmNotEnoughFundsForL1Fee,
                _ => L2BlockModuleCallError::EvmTransactionExecutionError(e.to_string()),
            }
        })?;

        if !*should_be_end_of_sys_txs && !result_and_state.result.is_success() {
            native_error!(
                "System transaction not successful. Result: {:?}",
                result_and_state.result
            );
            return Err(L2BlockModuleCallError::EvmSystemTransactionNotSuccessful);
        }
```

**File:** crates/evm/src/tests/sys_tx_tests.rs (L812-821)
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
```
