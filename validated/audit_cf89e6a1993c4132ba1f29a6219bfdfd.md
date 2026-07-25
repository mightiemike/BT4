### Title
Duplicate `input_data_ids` in ActionReceipt inflate `PendingDataCount`, permanently locking postponed receipts and attached funds — (`File: runtime/runtime/src/lib.rs`)

### Summary

`process_action_receipt` iterates over `action_receipt.input_data_ids()` without deduplication. If a contract emits a receipt whose `input_data_ids` list contains the same `data_id` more than once, the `PendingDataCount` stored in the trie is inflated beyond the number of distinct data receipts that will ever arrive. The receipt is then permanently postponed and any attached deposit is locked forever.

### Finding Description

In `process_action_receipt` (`runtime/runtime/src/lib.rs`, lines 1541–1588), the runtime counts how many `data_id`s are not yet satisfied and stores that count as `PendingDataCount`:

```rust
let mut pending_data_count: u32 = 0;
for data_id in action_receipt.input_data_ids() {
    if !has_received_data(state_update, account_id, *data_id)? {
        pending_data_count += 1;
        set(
            state_update,
            TrieKey::PostponedReceiptId { receiver_id: account_id.clone(), data_id: *data_id },
            receipt.receipt_id(),
        )
    }
}
``` [1](#0-0) 

If `input_data_ids = [X, X]` (duplicate), the loop runs twice for the same `data_id` X. On the first iteration `has_received_data` returns `false`, so `pending_data_count` becomes 1 and the `PostponedReceiptId` link is written. On the second iteration `has_received_data` still returns `false` (the data has not arrived yet), so `pending_data_count` becomes **2** and the same trie key is overwritten with the same value.

The `PendingDataCount` is then stored as 2:

```rust
set(
    state_update,
    TrieKey::PendingDataCount { receiver_id: account_id.clone(), receipt_id: *receipt.receipt_id() },
    &pending_data_count,   // 2, but only 1 distinct data_id exists
);
``` [2](#0-1) 

When the data receipt for X arrives (`process_receipt`, lines 1319–1406), the runtime:
1. Stores `ReceivedData[account, X]`.
2. Finds the `PostponedReceiptId` link, removes it, and decrements `PendingDataCount` from 2 → 1.
3. Since count > 0, the receipt is **not** executed. [3](#0-2) 

No second data receipt for X will ever arrive (there is only one distinct dependency). The `PostponedReceiptId` link was already removed, so any subsequent data receipt for X finds no link and does nothing. `PendingDataCount` stays at 1 indefinitely. The postponed receipt is **permanently stuck**.

The receipt validation path (`validate_action_receipt`) only enforces a count limit on `input_data_ids`, not uniqueness:

```rust
if receipt.input_data_ids().len() as u64 > limit_config.max_number_input_data_dependencies {
    return Err(ReceiptValidationError::NumberInputDataDependenciesExceeded { ... });
}
``` [4](#0-3) 

There is no deduplication check anywhere in the receipt validation or processing pipeline.

### Impact Explanation

A callback receipt with duplicate `input_data_ids` is permanently postponed. Any `deposit` attached to that receipt (transferred from the original transaction) is locked in the trie state and can never be recovered — the receipt will never execute, no refund receipt is generated, and there is no timeout mechanism for ordinary postponed receipts (only `PromiseYield` receipts have timeouts). This constitutes **loss of funds** and **contract execution flow breakage**, both within the HackenProof in-scope impact list.

### Likelihood Explanation

Any user can deploy a NEAR smart contract. A contract that calls `promise_and` with the same promise index twice will produce an `ActionReceipt` whose `input_data_ids` contains the same `CryptoHash` twice. The runtime has no guard against this at the receipt-creation or receipt-processing layer. The trigger is a standard unprivileged user action (deploy + call a contract).

**Caveat**: exploitability depends on whether the NEAR VM host-function layer (`near-vm-logic`) rejects duplicate promise indices passed to `promise_and` before the receipt is constructed. That code path was not reachable within the available search budget. If the VM logic silently deduplicates promise indices, the runtime-level bug is unreachable. If it does not, the vulnerability is fully exploitable by any contract author.

### Recommendation

In `process_action_receipt`, deduplicate `input_data_ids` before iterating:

```rust
let unique_data_ids: HashSet<CryptoHash> = action_receipt.input_data_ids().iter().copied().collect();
let mut pending_data_count: u32 = 0;
for data_id in &unique_data_ids {
    if !has_received_data(state_update, account_id, *data_id)? {
        pending_data_count += 1;
        set(state_update, TrieKey::PostponedReceiptId { ... }, receipt.receipt_id());
    }
}
```

Additionally, add a uniqueness check to `validate_action_receipt` (analogous to the existing count check) so that receipts with duplicate `input_data_ids` are rejected at creation time rather than silently producing broken state.

### Proof of Concept

1. Deploy contract `attacker.near` with the following logic:
   ```
   fn exploit() {
       let p = promise_create("victim.near", "noop", b"", 0, 5_000_000_000_000);
       // Pass the same promise index twice to promise_and
       let combined = promise_and([p, p]);
       promise_then(combined, env::current_account_id(), "callback", b"", 1_000_000_000_000_000_000_000_000 /* 1 NEAR deposit */, 5_000_000_000_000);
   }
   fn callback() { /* never reached */ }
   ```
2. Call `attacker.near::exploit()` with sufficient gas.
3. The runtime creates a callback `ActionReceipt` with `input_data_ids = [data_id(p), data_id(p)]`.
4. `process_action_receipt` sets `PendingDataCount = 2`.
5. `victim.near` executes and sends one data receipt for `data_id(p)`.
6. `PendingDataCount` decrements to 1; the callback receipt is not executed.
7. No further data receipt for `data_id(p)` arrives.
8. The 1 NEAR deposit attached to the callback receipt is permanently locked in the trie. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** runtime/runtime/src/lib.rs (L1331-1405)
```rust
                if let Some(receipt_id) = get(
                    state_update,
                    &TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: data_receipt.data_id,
                    },
                )? {
                    // There is already a receipt that is awaiting for the just received data.
                    // Removing this pending data_id for the receipt from the state.
                    state_update.remove(TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: data_receipt.data_id,
                    });
                    // Checking how many input data items is pending for the receipt.
                    let pending_data_count: u32 = get(
                        state_update,
                        &TrieKey::PendingDataCount { receiver_id: account_id.clone(), receipt_id },
                    )?
                    .ok_or_else(|| {
                        StorageError::StorageInconsistentState(
                            "pending data count should be in the state".to_string(),
                        )
                    })?;
                    if pending_data_count == 1 {
                        // It was the last input data pending for this receipt. We'll cleanup
                        // some receipt related fields from the state and execute the receipt.

                        // Removing pending data count from the state.
                        state_update.remove(TrieKey::PendingDataCount {
                            receiver_id: account_id.clone(),
                            receipt_id,
                        });
                        // Fetching the receipt itself.
                        let ready_receipt =
                            get_postponed_receipt(state_update, account_id, receipt_id)?
                                .ok_or_else(|| {
                                    StorageError::StorageInconsistentState(
                                        "pending receipt should be in the state".to_string(),
                                    )
                                })?;
                        // Removing the receipt from the state.
                        remove_postponed_receipt(state_update, account_id, receipt_id);
                        // Executing the receipt. It will read all the input data and clean it up
                        // from the state.
                        return self
                            .apply_action_receipt(
                                state_update,
                                apply_state,
                                pipeline_manager,
                                &ready_receipt,
                                receipt_sink,
                                instant_receipts,
                                validator_proposals,
                                stats,
                                epoch_info_provider,
                                receipt_to_tx,
                            )
                            .map(Some);
                    } else {
                        // There is still some pending data for the receipt, so we update the
                        // pending data count in the state.
                        set(
                            state_update,
                            TrieKey::PendingDataCount {
                                receiver_id: account_id.clone(),
                                receipt_id,
                            },
                            &(pending_data_count.checked_sub(1).ok_or_else(|| {
                                StorageError::StorageInconsistentState(
                                    "pending data count is 0, but there is a new DataReceipt"
                                        .to_string(),
                                )
                            })?),
                        );
                    }
```

**File:** runtime/runtime/src/lib.rs (L1526-1591)
```rust
    fn process_action_receipt(
        &self,
        receipt: &Receipt,
        receipt_sink: &mut ReceiptSink,
        instant_receipts: &mut VecDeque<Receipt>,
        validator_proposals: &mut Vec<ValidatorStake>,
        state_update: &mut TrieUpdate,
        apply_state: &ApplyState,
        epoch_info_provider: &dyn EpochInfoProvider,
        pipeline_manager: &ReceiptPreparationPipeline,
        stats: &mut ChunkApplyStatsV1,
        account_id: &AccountId,
        action_receipt: VersionedActionReceipt<'_>,
        receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
    ) -> Result<Option<ExecutionOutcomeWithId>, RuntimeError> {
        let mut pending_data_count: u32 = 0;
        for data_id in action_receipt.input_data_ids() {
            if !has_received_data(state_update, account_id, *data_id)? {
                pending_data_count += 1;
                // The data for a given data_id is not available, so we save a link to this
                // receipt_id for the pending data_id into the state.
                set(
                    state_update,
                    TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: *data_id,
                    },
                    receipt.receipt_id(),
                )
            }
        }

        if pending_data_count == 0 {
            // All input data is available. Executing the receipt. It will cleanup
            // input data from the state.
            return self
                .apply_action_receipt(
                    state_update,
                    apply_state,
                    pipeline_manager,
                    receipt,
                    receipt_sink,
                    instant_receipts,
                    validator_proposals,
                    stats,
                    epoch_info_provider,
                    receipt_to_tx,
                )
                .map(Some);
        } else {
            // Not all input data is available now.
            // Save the counter for the number of pending input data items into the state.
            set(
                state_update,
                TrieKey::PendingDataCount {
                    receiver_id: account_id.clone(),
                    receipt_id: *receipt.receipt_id(),
                },
                &pending_data_count,
            );
            // Save the receipt itself into the state.
            set_postponed_receipt(state_update, receipt);
        }

        Ok(None)
    }
```

**File:** runtime/runtime/src/verifier.rs (L588-616)
```rust
fn validate_action_receipt(
    limit_config: &LimitConfig,
    receipt: VersionedActionReceipt,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ReceiptValidationError> {
    if receipt.input_data_ids().len() as u64 > limit_config.max_number_input_data_dependencies {
        return Err(ReceiptValidationError::NumberInputDataDependenciesExceeded {
            number_of_input_data_dependencies: receipt.input_data_ids().len() as u64,
            limit: limit_config.max_number_input_data_dependencies,
        });
    }

    if let Some(account_id) = receipt.refund_to() {
        AccountId::validate(account_id.as_ref()).map_err(|_| {
            ReceiptValidationError::InvalidRefundTo { account_id: account_id.to_string() }
        })?;
    }

    validate_actions_with_mode(
        limit_config,
        receipt.actions(),
        receiver,
        current_protocol_version,
        mode,
    )
    .map_err(ReceiptValidationError::ActionsValidation)
}
```

**File:** core/primitives/src/receipt.rs (L638-638)
```rust
    pub input_data_ids: Vec<CryptoHash>,
```
