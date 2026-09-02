### Title
Sequencer permanently drops deposits from `DepositDataMempool` regardless of whether their system transaction actually succeeded, silently stranding BTC with no mint and no `failedDepositVault` redirect - ([File: crates/sequencer/src/runner.rs])

### Summary
`process_sys_txs` correctly skips (reverts and `continue`s) any `BridgeDeposit` system transaction that fails to execute successfully — for example when a malicious `recipient` contract's `receive()` consumes enough gas that the *entire* `deposit()` call exceeds `SYSTEM_TX_GAS_LIMIT` (1,000,000 gas) and the whole system transaction reverts, leaving none of `Bridge.sol`'s state changes (including the `!success` → `failedDepositVault` fallback) persisted. However, at block finalization, `produce_l2_block_inner` unconditionally calls `remove_deposits(&deposit_data)` using the **original, unfiltered** list returned by `fetch_deposits`, not the subset that actually succeeded and was included in `all_txs`. Any deposit whose system transaction failed during actual block production is therefore permanently removed from the mempool and never resubmitted, with its BTC/cBTC neither minted to `recipient` nor redirected to `failedDepositVault`.

### Finding Description
The binding to check is:
`removed_from_mempool(deposit) == true` ⇒ `(cBTC minted to recipient) OR (cBTC sent to failedDepositVault)` for that deposit's `txId`.

Trace:
1. `fetch_deposits(limit_per_block)` returns a snapshot `deposit_data: Vec<Deposit>` without removing anything ( [1](#0-0) ), stored into the local variable `deposit_data` in `produce_l2_block_inner` ( [2](#0-1) ).
2. This same `deposit_data` is passed into `dry_run_transactions` → `produce_and_run_system_transactions` → `process_sys_txs` ( [3](#0-2) ).
3. `process_sys_txs` builds a `BridgeDeposit` system tx per deposit item with a fixed `gas_limit: SYSTEM_TX_GAS_LIMIT` ( [4](#0-3) ) equal to 1,000,000 ( [5](#0-4) ). If applying that tx returns `L2BlockModuleCallError::EvmSystemTransactionNotSuccessful` (which `execute_multiple_tx` returns for *any* non-successful system-tx execution result, including a full out-of-gas revert — [6](#0-5) ), the code reverts the working set for that tx and `continue`s to the next event, silently excluding the deposit from the produced block ( [7](#0-6) ).
4. Critically, `produce_l2_block_inner` never re-derives which deposits actually succeeded; after committing the block it calls `self.deposit_mempool.lock().remove_deposits(&deposit_data)` using the **original fetch list**, unconditionally ( [8](#0-7) ). `remove_deposits` removes every deposit in that list from both `accepted_deposit_txs` and `pending_deposits` ( [9](#0-8) ) — there is no code path that re-adds a failed deposit or resubmits it in a later block.
5. `Bridge.sol::deposit`'s `recipient.call{value: depositAmount}("")` ( [10](#0-9) ) only catches an inner-call failure (ordinary revert or exhaustion of the *forwarded* 63/64 gas) and redirects to `failedDepositVault`; it cannot catch the case where the recipient's `receive()` consumes enough gas that the *remaining* 1/64-reserved gas plus prior headroom is insufficient to execute the subsequent `emit`/vault-call bytecode, causing the entire `deposit()` call — and thus the whole system transaction — to run out of gas and revert with no state persisted.
6. The RPC-level guard in `citrea_sendRawDepositTransaction` (`get_call` simulated with the same `SYSTEM_TX_GAS_LIMIT`, [11](#0-10) ) only prevents *deterministic* gas bombs from ever entering the mempool. It does not prevent an attacker-controlled recipient contract whose gas consumption is conditioned on mutable, attacker-controllable state (e.g., a flag or balance the attacker flips via an ordinary transaction *after* successful simulation but *before* the sequencer actually includes the already-queued deposit) from passing simulation cheaply and then gas-bombing the real inclusion.
7. Once that happens, step 4's unconditional `remove_deposits` call permanently discards the deposit from `DepositDataMempool`, with no mint to `recipient`, no redirect to `failedDepositVault`, and no further retry — breaking the binding.

### Impact Explanation
Critical: BTC genuinely locked on Bitcoin via a legitimate Clementine `moveTx` (the attacker's own deposit, directed to a recipient contract the attacker controls) can be made to correspond to a deposit blob that is accepted by `citrea_sendRawDepositTransaction`, silently dropped from the sequencer's `DepositDataMempool` on the first attempted inclusion whose system transaction fails, and never resubmitted. The resulting cBTC is neither minted to `recipient` nor moved to `failedDepositVault` — the BTC is permanently unrecoverable from Citrea's side for that specific `txId`, since `processedTxIds[txId]` was never set (the whole tx reverted) yet the deposit blob will never be offered to the chain again. This is a one-shot, deterministic loss per affected deposit, not merely a retry/DoS issue, and it stems directly from the mismatch between `fetch_deposits`'s snapshot and `remove_deposits`'s unconditional use of that same snapshot in `produce_l2_block_inner`.

### Likelihood Explanation
The attacker only needs: (1) the ability to make a real Bitcoin deposit through the legitimate Clementine flow directing funds to a contract they control (standard permissionless deposit path, cost = real BTC deposit amount + fees), (2) the ability to call `citrea_sendRawDepositTransaction` and ordinary EVM transactions (both permissionless), and (3) a window between successful RPC-level simulation and actual sequencer inclusion (deposits sit in a FIFO mempool, and inclusion is not instantaneous) during which they flip mutable state read by their contract's `receive()` to switch it from cheap to gas-bomb behavior. No sequencer, prover, DA, or Clementine privilege is required. The underlying code defect (unconditional `remove_deposits(&deposit_data)` on the unfiltered fetch list) is unconditionally present and would strand any deposit that fails for any reason during real inclusion after passing simulation, regardless of the exact griefing technique used to produce that divergence.

### Recommendation
Track, per produced block, exactly which deposit blobs were actually included (present in `all_txs`/succeeded in `process_sys_txs`), and only call `remove_deposits` with that filtered subset in `produce_l2_block_inner`, leaving failed deposits in the mempool so they are retried in a later block. Additionally, since `Bridge.sol::deposit`'s external call to `recipient` can consume unbounded (up to 63/64 of remaining) gas, consider capping the gas forwarded to `recipient.call` in `Bridge.sol` so that a malicious recipient cannot cause the surrounding `deposit()` call — and therefore the whole system transaction — to run out of gas.

### Proof of Concept
```
cargo test -p citrea-sequencer --test <sequencer_behaviour> -- deposit_gas_bomb_permanently_dropped
```
Test plan:
1. Deploy `GriefRecipient` with a `flag` storage slot and a `receive()` that does cheap work if `!flag`, and burns ~950,000 gas (busy loop) if `flag == true`; add a public `setFlag(bool)` setter.
2. Construct a valid `moveTx`/Merkle proof (as in `Bridge.t.sol`'s `doDeposit()`/existing sys-tx test fixtures) whose witness script encodes `GriefRecipient`'s address as `recipient`.
3. Call `citrea_sendRawDepositTransaction` with this deposit while `flag == false`; assert it is accepted (`Ok(())`), confirming `deposit_mempool.pending_deposits` contains its `calc_tx_id`.
4. Call `setFlag(true)` via a normal `eth_sendRawTransaction`.
5. Allow the sequencer to produce the next L2 block(s) including this deposit.
6. Assert: `recipient_account.balance == 0` (no mint) AND `failedDepositVault.balance` unchanged (no redirect) AND `bridge.processedTxIds(txId) == false` (tx never persisted) — confirming the deposit's system tx reverted.
7. Assert the deposit's `txId`/`calc_tx_id` is **not** present in `deposit_mempool.pending_deposits`/`accepted_deposit_txs` after this block (i.e., it was removed via the unfiltered `remove_deposits` call).
8. Produce further blocks and assert the deposit is never resubmitted or included in any subsequent block, and `recipient`/`failedDepositVault` balances remain unchanged indefinitely — violating the binding that every valid deposit results in a mint or a vault redirect.

### Citations

**File:** crates/sequencer/src/deposit_data_mempool.rs (L57-69)
```rust
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

**File:** crates/sequencer/src/deposit_data_mempool.rs (L78-109)
```rust
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

**File:** crates/sequencer/src/runner.rs (L1678-1697)
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
```

**File:** crates/evm/src/evm/system_events.rs (L14-15)
```rust
/// Gas limit for system transactions.
pub const SYSTEM_TX_GAS_LIMIT: u64 = 1_000_000;
```

**File:** crates/evm/src/evm/system_events.rs (L73-82)
```rust
        SystemEvent::BridgeDeposit(params) => TxEip1559 {
            to: TxKind::Call(BridgeWrapper::address()),
            input: BridgeWrapper::deposit(params),
            nonce,
            chain_id,
            value: U256::ZERO,
            gas_limit: SYSTEM_TX_GAS_LIMIT,
            max_fee_per_gas: u64::MAX as u128,
            ..Default::default()
        },
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

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L230-240)
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
```

**File:** crates/sequencer/src/rpc.rs (L339-357)
```rust
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
```
