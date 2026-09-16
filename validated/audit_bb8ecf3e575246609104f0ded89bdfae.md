This is confirmed: `consume_l1_to_l2_message` at the OS level only appends the message to `outputs.messages_to_l2` (the block's L1-message output segment); it does not itself look up or decrement an on-chain "already-consumed" counter — the actual double-consumption protection is delegated entirely to L1 (via `l1ToL2Messages` mapping / `l1ToL2MessageNonce`), checked only when the state update is submitted to the core contract, well after L2 consensus has already committed the block [1](#0-0) . This confirms the sequencer's local `TransactionManager` bookkeeping (`records`, `Committed`/`Consumed` state) is the *only* L2-side gate preventing re-inclusion of an L1 handler tx that was already committed, since L1-side detection only surfaces at settlement time, long after the block is finalized by consensus.

### Title
Premature deletion of a Committed L1-handler transaction record on late `finalize_cancellation` allows re-inclusion (double execution) of an already-processed L1→L2 message - (File: crates/apollo_l1_events/src/transaction_manager.rs)

### Summary
`TransactionManager::finalize_cancellation` unconditionally deletes the transaction's record whenever an L1 "cancellation finalized" event arrives, regardless of whether the transaction has already reached the `Committed` state in the interim. This mirrors the Karak finding's root cause: a "finalize" action is executed without re-validating that the previously-recorded state (registration / commitment) is still applicable, silently discarding critical bookkeeping and re-opening a state that should be terminal.

### Finding Description
`request_cancellation` sets a transaction's state to `CancellationStartedOnL2` as soon as an L1 cancellation-start event is scraped [2](#0-1) . Crucially, `is_validatable()` still returns `true` in this state (it only excludes `Committed`, `CancelledOnL2`, and `Consumed`) [3](#0-2) , and the local "L2 cancellation timelock" that would eventually flip the tx to `CancelledOnL2` is independent of, and typically much shorter than, the real L1-side cancellation delay that must elapse before `finalize_cancellation` is actually triggered by the L1 event scraper. This creates a window in which the transaction can still be validated and committed into an L2 block (`commit_txs` → `mark_committed`, setting `state = Committed`) while an L1 cancellation is concurrently in flight [4](#0-3) .

When the L1 cancellation is later finalized, `finalize_cancellation` is invoked. It explicitly proceeds "regardless of the state of the tx in the record" — only logging a warning if the state isn't `CancellationStartedOnL2` — and then unconditionally calls `mark_cancellation_finalized_on_l1()` and removes the record from `self.records` [5](#0-4) . This deletes the `Committed` marker for a transaction that has already been executed in a prior L2 block. Since `records` is the *only* structure that tracks "already-included-on-L2" status (`is_committed`, checked by `validate_tx` to return `AlreadyIncludedOnL2`) [6](#0-5) , once the record is gone, a subsequent `add_tx` call (e.g., from a re-scrape, state-sync catch-up, or a duplicated/replayed `LogMessageToL2` observation) recreates a fresh record via `create_record_if_not_exist`, defaulting to `TransactionState::Pending` [7](#0-6) [8](#0-7) . The transaction is now indistinguishable from a brand-new, never-executed L1 message and becomes proposable/validatable again, violating the documented invariant "Once removed from this index, a transaction will never be proposed again" [9](#0-8) .

At the actual execution layer, this is not caught: the Starknet OS's `execute_l1_handler_transaction` simply calls `consume_l1_to_l2_message`, which only appends a `MessageToL2Header` entry to the block's output segment for later L1-side verification — it performs no on-L2 check against a "already consumed" set [10](#0-9) . The `TransactionManager`'s local bookkeeping is therefore the sole L2-side safeguard against double inclusion of the same L1 message before L1 settlement.

### Impact Explanation
If a duplicate/rescraped `add_tx` occurs for a tx_hash whose record was erroneously deleted while `Committed`, the L1 handler transaction can be validated and proposed a second time and executed by the blockifier/OS in a later block, causing the associated contract entry point (e.g., a bridge deposit handler) to run twice with the same L1 message payload. This is a concrete double-execution/duplicate-funds-minting risk. Even absent an immediate double-execution (if no re-scrape happens to coincide), it silently and permanently loses the sequencer's record that this L1 message was already handled, degrading a safety invariant relied on by `validate_tx`. Because the OS/blockifier layer does not independently guard against this, the divergence would only be caught (and rejected) at L1 settlement, meaning the block could already have been accepted by L2 consensus with an invalid/duplicated state transition, at which point the chain's state updates to L1 would revert — a form of committed-root/finality failure that matches the "wrong committed root" / "network unable to confirm new transactions" impact criteria.

### Likelihood Explanation
The trigger requires: (1) an operator to submit a cancellation-start request on L1 for a message shortly before it gets picked up and committed on L2 (a legitimate, unprivileged, permissionless L1 action any L1 message sender can perform), and (2) the scraper/provider later observing an `add_tx`-triggering event for the same hash after the finalize-cancellation event removed the record (e.g., via state-sync catch-up replay or scraper re-observation, both of which are explicitly anticipated code paths in `add_tx`'s "HashOnly -> Full" and double-scrape handling logic). Both preconditions are plausible under normal, permissionless operation (an L1 sender racing their own message's cancellation against L2 inclusion, combined with routine catch-up/rescraping), making this a realistically reachable, not purely theoretical, race.

### Recommendation
In `finalize_cancellation`, before deleting the record, check whether the transaction is already `Committed` (or `Consumed`); if so, do not delete the record — instead keep it (or transition to a distinct terminal state) so that `is_committed`/`is_consumed` checks continue to reject any future re-addition of the same tx_hash. Only remove records for transactions that were genuinely never included on L2.

### Proof of Concept
1. L1 message `M` (tx_hash `H`) is sent to L2; sequencer scrapes it and stores it as `Pending`.
2. L1 sender calls `startL1ToL2MessageCancellation` for `M`; scraper emits a cancellation-start event; `request_cancellation(H, t1)` sets state to `CancellationStartedOnL2` (still validatable since not yet past the local `cancellation_timelock`).
3. Before the L2-side timelock elapses, a proposer validates and includes `H` in block `N`; `commit_txs([H], [])` marks it `Committed`.
4. After the L1-side cancellation delay elapses, `startL1ToL2MessageCancellation`'s companion finalize event is scraped; `finalize_cancellation(H)` is called — it logs a warning ("not in the cancellation started on L2 state, but in the Committed state") but still calls `mark_cancellation_finalized_on_l1()` and `self.records.remove(&H)`.
5. A subsequent scraper poll or state-sync catch-up re-observes the original `LogMessageToL2` event for `M` (or a duplicate submission with the same payload/hash) and calls `add_tx(tx, ...)`; since the record no longer exists, `create_record_if_not_exist` creates a fresh `Pending` record.
6. `H` is now proposable/validatable again and can be included in a later block, causing the L1 handler's entry point to execute a second time for the same message.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-519)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );

    %{ EndTx %}
    return ();
}

// Guess the execution context of an invoke transaction (either invoke function or L1 handler).
// Leaves 'execution_info.tx_info' and 'deprecated_tx_info' empty - should be
// filled later on.
func get_invoke_tx_execution_context{range_check_ptr, contract_state_changes: DictAccess*}(
    block_context: BlockContext*, entry_point_type: felt, entry_point_selector: felt
) -> (tx_execution_context: ExecutionContext*) {
    alloc_locals;
    local contract_address;
    %{ ContractAddress %}
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );
    let (tx_info_ptr: TxInfo*) = alloc();
    let (deprecated_tx_info_ptr: DeprecatedTxInfo*) = alloc();
    local calldata_size;
    local calldata: felt*;
    %{ TxCalldata %}
    local tx_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=entry_point_type,
        class_hash=state_entry.class_hash,
        calldata_size=calldata_size,
        calldata=calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info_ptr,
            caller_address=ORIGIN_ADDRESS,
            contract_address=contract_address,
            selector=entry_point_selector,
        ),
        deprecated_tx_info=deprecated_tx_info_ptr,
    );
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);

    return (tx_execution_context=tx_execution_context);
}

// Adds 'tx' with the given 'nonce' to 'outputs.messages_to_l2'.
func consume_l1_to_l2_message{outputs: OsCarriedOutputs*}(
    execution_context: ExecutionContext*, nonce: felt
) {
    assert_not_zero(execution_context.calldata_size);
    // The payload is the calldata without the from_address argument (which is the first).
    let payload: felt* = execution_context.calldata + 1;
    tempvar payload_size = execution_context.calldata_size - 1;

    tempvar execution_info = execution_context.execution_info;

    // Write the given transaction to the output.
    assert [outputs.messages_to_l2] = MessageToL2Header(
        from_address=[execution_context.calldata],
        to_address=execution_info.contract_address,
        nonce=nonce,
        selector=execution_info.selector,
        payload_size=payload_size,
    );

    let message_payload = cast(outputs.messages_to_l2 + MessageToL2Header.SIZE, felt*);
    memcpy(dst=message_payload, src=payload, len=payload_size);

    let (outputs) = os_carried_outputs_new(
        messages_to_l1=outputs.messages_to_l1,
        messages_to_l2=outputs.messages_to_l2 + MessageToL2Header.SIZE +
        outputs.messages_to_l2.payload_size,
    );
    return ();
}
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L34-39)
```rust
impl TransactionRecord {
    /// Create a new transaction record from a transaction payload, epoch is 0 by default, allowing
    /// the transaction to always be stageable, since the transaction manager's epoch starts at one.
    pub fn new(payload: TransactionPayload) -> Self {
        Self::from(payload)
    }
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L77-101)
```rust
    pub fn mark_cancellation_request(
        &mut self,
        timestamp: BlockTimestamp,
    ) -> Option<BlockTimestamp> {
        let tx_hash = self.tx.tx_hash();
        // Once committed on L2, cancellation requests are only recorded for debugging purposes, but
        // not processed.
        if self.is_committed() {
            warn!(
                "L1 handler transaction {tx_hash} was not marked for cancellation started on L2 \
                 as it is already committed."
            )
        } else {
            info!("Marking L1 handler transaction {tx_hash} as cancellation started on L2.");
            self.state = TransactionState::CancellationStartedOnL2;
        }

        match self.cancellation_requested_at {
            Some(existing) => Some(existing),
            None => {
                self.cancellation_requested_at = Some(timestamp);
                None
            }
        }
    }
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L179-181)
```rust
    pub fn is_validatable(&self) -> bool {
        !self.is_committed() && !self.is_cancelled() && !self.is_consumed()
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L38-39)
```rust
    /// Invariant: contains all hashes of transactions that are proposable, and only them.
    /// Invarariant 2: Once removed from this index, a transaction will never be proposed again.
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L116-145)
```rust
    pub fn validate_tx(&mut self, tx_hash: TransactionHash, unix_now: u64) -> ValidationStatus {
        let current_staging_epoch_cloned = self.current_staging_epoch;

        let policy = TransactionRecordPolicy {
            cancellation_timelock: self.config.l1_handler_cancellation_timelock_seconds,
        };

        let validation_status = self.with_record(tx_hash, |record| {
            // If the current time affects the state, update state now.
            record.update_time_based_state(unix_now, policy);
            if !record.is_validatable() {
                match record.state {
                    TransactionState::Committed => {
                        InvalidValidationStatus::AlreadyIncludedOnL2.into()
                    }
                    TransactionState::CancelledOnL2 => {
                        InvalidValidationStatus::CancelledOnL2.into()
                    }
                    TransactionState::Consumed => InvalidValidationStatus::ConsumedOnL1.into(),
                    _ => unreachable!(),
                }
            } else if record.try_mark_staged(current_staging_epoch_cloned) {
                ValidationStatus::Validated
            } else {
                InvalidValidationStatus::AlreadyIncludedInProposedBlock.into()
            }
        });

        validation_status.unwrap_or(InvalidValidationStatus::NotFound.into())
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L147-166)
```rust
    pub fn commit_txs(
        &mut self,
        committed_txs: &[TransactionHash],
        rejected_txs: &[TransactionHash],
    ) {
        self.rollback_staging();

        for &tx_hash in committed_txs {
            self.create_record_if_not_exist(tx_hash);
            self.with_record(tx_hash, |r| r.mark_committed()).unwrap();
        }
        for &tx_hash in rejected_txs {
            self.with_record(tx_hash, |r| r.mark_rejected()).expect(
                "Rejected L1 handler tx has no record. Unreachable: all L1 handler txs in a \
                 committed block were validated as known (validation rejects unknown hashes), \
                 sync commits with empty rejected_txs, and records are only removed via L1 \
                 cancellation/consumption, which can't race a block.",
            );
        }
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L221-245)
```rust
    pub fn finalize_cancellation(&mut self, tx_hash: TransactionHash) {
        let Some(record) = self.records.get(&tx_hash) else {
            info!(
                "Attempted to finalize cancellation for non-existent transaction: {tx_hash}. This \
                 can happen if the transaction was too old to be scraped (e.g. it was created \
                 before we started scraping)."
            );
            return;
        };

        // Regardless of the state of the tx in the record, if we get the cancellation event from
        // the L1 contract, we delete this tx from the records and from the proposable index, even
        // if it was Pending and ready to be proposed (which is not supposed to happen, hence the
        // warning).
        if record.state != TransactionState::CancellationStartedOnL2 {
            warn!(
                "Attempted to finalize cancellation for transaction {tx_hash} that is not in the \
                 cancellation started on L2 state, but in the {:?} state.",
                record.state
            );
        }
        // This will also call maintain_indices to remove the tx from the proposable index.
        self.with_record(tx_hash, |r| r.mark_cancellation_finalized_on_l1());
        self.records.remove(&tx_hash);
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L351-353)
```rust
    fn create_record_if_not_exist(&mut self, hash: TransactionHash) -> bool {
        self.records.insert(hash, TransactionRecord::new(hash.into()))
    }
```
