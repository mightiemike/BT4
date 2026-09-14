### Title
Unbounded instant-receipt drain loop bypasses the chunk compute/gas limit via chained `DeleteAccount` receipts - (File: `runtime/runtime/src/lib.rs`)

### Summary
`process_receipt_and_instant_receipts` drains the `instant_receipts` queue in a `while` loop that has **no** compute/gas-limit check, unlike every other receipt-processing loop in the runtime. An attacker can get one action receipt to spawn many single-action `DeleteAccount` receipts (which are classified as "instant"), all of which then get executed unconditionally in the same chunk regardless of the configured `compute_limit`, letting a single transaction force a chunk to perform far more state-transition work than the gas/compute limit is designed to allow.

### Finding Description
`Receipt::is_instant_receipt` classifies any action receipt containing exactly one `DeleteAccount` action (and no `input_data_ids`) as an "instant receipt", along with `PromiseYield` receipts: [1](#0-0) 

This classification is applied purely structurally to *every* newly generated receipt, regardless of what produced it: [2](#0-1) 

Instant receipts are pushed to `instant_receipts` and then drained by `process_receipt_and_instant_receipts`: [3](#0-2) 

Note the inner `while let Some(instant_receipt) = processing_state.instant_receipts.pop_front()` loop has **no** `total.compute >= compute_limit` (or storage-proof-size) check before processing each item. Contrast this with the three sibling loops that *do* check the limit before every iteration: [4](#0-3) [5](#0-4) [6](#0-5) 

The doc comment on `is_instant_receipt` itself flags the risk: "Instant receipts generally shouldn't emit new instant receipts, as it could lead to infinitely many receipts being executed in a single chunk" — but the runtime relies entirely on the *convention* that current instant-receipt shapes (`PromiseYield`, single `DeleteAccount`) don't recursively spawn more instant receipts; it does not enforce any bound on how many instant receipts a single top-level receipt may enqueue in the first place.

A single `FunctionCall` action receipt can, via the promise API (`promise_batch_create` + `promise_batch_action_delete_account`), emit many separate outgoing action receipts, each carrying exactly one `DeleteAccount` action targeting a distinct receiver account. Every one of those newly created receipts matches `is_instant_receipt()` and is routed into `instant_receipts` instead of the normal `ReceiptSink`. All of them are then executed back-to-back by the unbounded `while` loop, with no re-check of `compute_limit`, before returning control to the outer per-receipt loops.

Each `DeleteAccount` action performs non-trivial trie work via `remove_account`, which iterates and removes every access key, gas-key nonce, and contract-data entry belonging to the deleted account: [7](#0-6) 

The only cap on this per-account cost is `MAX_ACCOUNT_DELETION_STORAGE_USAGE` (10,000 bytes) checked in `action_delete_account`: [8](#0-7) 

That cap bounds the cost of *one* deletion, but nothing bounds the *number* of such deletions that can be queued as instant receipts and drained in a single chunk outside of `compute_limit` accounting.

### Impact Explanation
The attacker pre-creates N cheap sub-accounts (paying only ordinary account-creation/storage-stake costs) and then submits one `FunctionCall` receipt that issues N single-action `DeleteAccount` promises against them. All N resulting receipts are instant and get executed in the same chunk-apply call without the `compute_limit`/proof-size gating that governs every other receipt source. This lets a single transaction cause the chunk-applying node to perform an amount of trie-mutation work effectively unbounded by the protocol's gas limit, inflating chunk-application latency for every validator/RPC node that must apply the same chunk. This is a transaction-triggered resource-exhaustion/DoS vector on the deterministic state-transition function itself (matches the "transaction-triggered halt"/DoS class called out as acceptable impact).

### Likelihood Explanation
Reachable by any unprivileged account: it only requires (a) funding and creating N ordinary sub-accounts (a normal, permitted operation) and (b) a single contract call that batches N `DeleteAccount` promises against them. No validator or node compromise is needed — this is a pure single-transaction/contract-call path through `apply_action_receipt` → `is_instant_receipt` → `process_receipt_and_instant_receipts`.

### Recommendation
Add the same `total.compute >= compute_limit` (and storage-proof-size) check inside the `instant_receipts` drain loop in `process_receipt_and_instant_receipts`, and/or cap the number/aggregate cost of instant receipts a single receipt's execution may enqueue, re-queuing the excess as ordinary (compute-limit-gated) receipts (e.g. via the delayed-receipt queue) instead of executing them unconditionally.

### Proof of Concept
1. Attacker account `A` creates N sub-accounts `a1.A … aN.A` (paying standard storage stake), each with minimal state (no local contract, one access key or fewer, well under the 10,000-byte deletion cap).
2. Attacker deploys/calls a contract from `A` (or from any account) whose method, within one `FunctionCall` receipt, calls `promise_batch_create(ai.A)` + `promise_batch_action_delete_account(idx, beneficiary)` for each `i in 1..N`, producing N outgoing `Receipt`s that each contain exactly `[Action::DeleteAccount(_)]` with empty `input_data_ids`.
3. When this `FunctionCall` receipt's actions finish (`runtime/runtime/src/lib.rs:1214-1234`), each of the N generated receipts is classified `is_instant_receipt() == true` and pushed onto `instant_receipts`.
4. `process_receipt_and_instant_receipts` (`runtime/runtime/src/lib.rs:2747-2776`) then drains all N of them in its unconditional `while` loop, performing N `remove_account` trie-removal passes in the same chunk with no `compute_limit` check gating admission — regardless of how much compute budget the chunk had left after the originating receipt.
5. Repeating this pattern across chunks/transactions, an attacker can force disproportionate chunk-application work per submitted transaction relative to its charged gas, degrading chunk-application throughput for all nodes applying that chunk.

### Citations

**File:** core/primitives/src/receipt.rs (L468-491)
```rust
    /// An instant receipt is a receipt which should be processed immediately after the receipt that
    /// produced it, in the same chunk, irrespective of the gas limit.
    /// The expectation is that applying an instant receipt is a quick operation (e.g. setting a few values in the state).
    /// Instant receipts generally shouldn't emit new instant receipts, as it could lead to
    /// infinitely many receipts being executed in a single chunk.
    pub fn is_instant_receipt(&self) -> bool {
        match self.versioned_receipt() {
            VersionedReceiptEnum::PromiseYield(_) => {
                // PromiseYield receipts are instant receipts.
                // Applying a PromiseYield receipt is one trie write, it's okay to make it an instant receipt.
                true
            }
            VersionedReceiptEnum::Action(action_receipt) => {
                // Action receipts containing a single DeleteAccount action and no input
                // promises are instant receipts.
                // Deleting an account is a quick trie operation, it's okay to make it instant.
                matches!(action_receipt.actions(), [Action::DeleteAccount(_)])
                    && action_receipt.input_data_ids().is_empty()
            }
            VersionedReceiptEnum::Data(_)
            | VersionedReceiptEnum::PromiseResume(_)
            | VersionedReceiptEnum::GlobalContractDistribution(_) => false,
        }
    }
```

**File:** runtime/runtime/src/lib.rs (L1214-1234)
```rust
                let is_action = matches!(
                    new_receipt.receipt(),
                    ReceiptEnum::Action(_)
                        | ReceiptEnum::PromiseYield(_)
                        | ReceiptEnum::ActionV2(_)
                        | ReceiptEnum::PromiseYieldV2(_)
                );

                if new_receipt.is_instant_receipt() {
                    // Instant receipts are not sent as outgoing receipts, they will be processed immediately.
                    instant_receipts.push_back(new_receipt);
                } else {
                    // Send out the receipt as an outgoing receipt.
                    if let Err(e) = receipt_sink.forward_or_buffer_receipt(
                        new_receipt,
                        apply_state,
                        state_update,
                    ) {
                        return Some(Err(e));
                    }
                }
```

**File:** runtime/runtime/src/lib.rs (L2513-2522)
```rust
        for receipt in &local_receipts {
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                processing_state.delayed_receipts.push(
                    &mut processing_state.state_update,
                    &receipt,
                    &processing_state.apply_state,
                )?;
            } else {
```

**File:** runtime/runtime/src/lib.rs (L2591-2606)
```rust
        loop {
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                break;
            }

            let receipt = if let Some(receipt) = processing_state
                .delayed_receipts
                .pop(&mut processing_state.state_update, &processing_state.apply_state.config)?
            {
                receipt.into_receipt()
            } else {
                // Break loop if there are no more receipts to be processed.
                break;
            };
```

**File:** runtime/runtime/src/lib.rs (L2704-2711)
```rust
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                processing_state.delayed_receipts.push(
                    &mut processing_state.state_update,
                    receipt,
                    &processing_state.apply_state,
                )?;
```

**File:** runtime/runtime/src/lib.rs (L2747-2776)
```rust
    /// Process a receipt and then immediately process all newly generated instant receipts.
    fn process_receipt_and_instant_receipts(
        &self,
        receipt: &Receipt,
        processing_state: &mut ApplyProcessingReceiptState,
        receipt_sink: &mut ReceiptSink,
        validator_proposals: &mut Vec<ValidatorStake>,
    ) -> Result<(), RuntimeError> {
        self.process_receipt_with_metrics(
            receipt,
            processing_state,
            receipt_sink,
            validator_proposals,
        )?;

        while let Some(instant_receipt) = processing_state.instant_receipts.pop_front() {
            self.process_receipt_with_metrics(
                &instant_receipt,
                processing_state,
                receipt_sink,
                validator_proposals,
            )?;
            processing_state.processed_receipts.push(ProcessedReceipt {
                receipt: instant_receipt,
                source: ReceiptSource::Instant,
            });
        }

        Ok(())
    }
```

**File:** core/store/src/utils/mod.rs (L504-553)
```rust
/// Removes account, code and all access keys and gas keys associated to it.
pub fn remove_account(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
) -> Result<RemoveAccountResult, StorageError> {
    state_update.remove(TrieKey::Account { account_id: account_id.clone() });
    state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });

    let mut gas_key_nonce_count: usize = 0;
    let mut gas_key_nonce_total_key_bytes: usize = 0;

    // Removing access keys and gas key nonces
    let lock = state_update.trie().lock_for_iter();
    let mut keys_to_remove: Vec<TrieKey> = Vec::new();
    for raw_key in state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_access_keys(account_id), &lock)?
    {
        let raw_key = raw_key?;
        let key_handle = trie_key_parsers::parse_key_handle_from_access_key_key(
            &raw_key, account_id,
        )
        .map_err(|_e| {
            StorageError::StorageInconsistentState(
                "Can't parse key handle from raw key for AccessKey".to_string(),
            )
        })?;
        let nonce_index =
            trie_key_parsers::parse_nonce_index_from_gas_key_key(&raw_key, account_id, &key_handle)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse nonce index from raw key for AccessKey".to_string(),
                    )
                })?;
        if let Some(index) = nonce_index {
            gas_key_nonce_count += 1;
            gas_key_nonce_total_key_bytes += raw_key.len();
            keys_to_remove.push(TrieKey::gas_key_nonce(
                account_id.clone(),
                key_handle.clone(),
                index,
            ));
        } else {
            keys_to_remove.push(TrieKey::access_key(account_id.clone(), key_handle.clone()));
        }
    }
    drop(lock);

    for trie_key in keys_to_remove {
        state_update.remove(trie_key);
    }
```

**File:** runtime/runtime/src/actions.rs (L330-369)
```rust
pub(crate) fn action_delete_account(
    state_update: &mut TrieUpdate,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    receipt: &Receipt,
    result: &mut ActionResult,
    account_id: &AccountId,
    delete_account: &DeleteAccountAction,
    config: &RuntimeConfig,
    current_protocol_version: ProtocolVersion,
) -> Result<(), StorageError> {
    let account_ref = account.as_ref().unwrap();
    let account_storage_usage = if ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
        .enabled(current_protocol_version)
    {
        let contract_storage = get_contract_storage_usage(state_update, account_id, account_ref)?;
        account_ref.storage_usage().saturating_sub(contract_storage)
    } else {
        // Legacy behavior: only subtracts local contract code, misses the
        // global contract identifier overhead.
        let account_storage_usage = account_ref.storage_usage();
        let code_len = get_code_len_or_default(
            state_update,
            account_id.clone(),
            account_ref.local_contract_hash().unwrap_or_default(),
        )?;
        debug_assert!(
            code_len == 0 || account_storage_usage > code_len,
            "account storage usage should be larger than code size. storage usage: {}, code size: {}",
            account_storage_usage,
            code_len
        );
        account_storage_usage.saturating_sub(code_len)
    };
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
```
