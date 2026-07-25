### Title
Duplicate `input_data_ids` in `ActionReceipt` inflates `PendingDataCount`, permanently locking postponed receipts and attached deposits - (`runtime/runtime/src/lib.rs`)

---

### Summary

`process_action_receipt` iterates over `input_data_ids` without deduplication. A contract that emits an `ActionReceipt` with a repeated `data_id` (e.g. `[X, X]`) causes `pending_data_count` to be set to 2, while the trie key `PostponedReceiptId[X]` is written only once (the second write silently overwrites the first with the same value). When the single `DataReceipt` for `X` arrives, the runtime decrements `PendingDataCount` from 2 to 1 and removes `PostponedReceiptId[X]`. Because that trie key is now gone, no future `DataReceipt` can trigger a second decrement; `PendingDataCount` stays at 1 forever and the postponed receipt is permanently stuck. Any `deposit` attached to that receipt is irrecoverably lost.

---

### Finding Description

**Root cause — `process_action_receipt`** (`runtime/runtime/src/lib.rs`, lines 1541–1556):

```rust
let mut pending_data_count: u32 = 0;
for data_id in action_receipt.input_data_ids() {          // ← no dedup
    if !has_received_data(state_update, account_id, *data_id)? {
        pending_data_count += 1;                          // ← incremented per occurrence
        set(
            state_update,
            TrieKey::PostponedReceiptId { receiver_id: account_id.clone(), data_id: *data_id },
            receipt.receipt_id(),                         // ← same key overwritten
        )
    }
}
``` [1](#0-0) 

With `input_data_ids = [X, X]` and `X` not yet in state:

| Step | `pending_data_count` | `PostponedReceiptId[X]` |
|------|----------------------|-------------------------|
| iter 1 | 1 | receipt_id |
| iter 2 | **2** | receipt_id (overwrite, same value) |

`PendingDataCount` is stored as **2**, but only one distinct trie key exists.

**Data-receipt processing** (`runtime/runtime/src/lib.rs`, lines 1319–1405):

When `DataReceipt` for `X` arrives:
1. `PostponedReceiptId[X]` is found → removed from trie.
2. `PendingDataCount` decremented: 2 → 1.
3. Count ≠ 1 → receipt is **not** executed. [2](#0-1) 

`PostponedReceiptId[X]` is now gone. No second `DataReceipt` for `X` will ever arrive. `PendingDataCount` is permanently 1. The postponed receipt and its attached deposit are frozen in state indefinitely.

**No validation prevents duplicate `input_data_ids`** — `validate_receipt` and `validate_actions_with_mode` check action counts, gas limits, and receiver IDs, but impose no uniqueness constraint on `input_data_ids`. [3](#0-2) 

The `input_data_ids` field is declared as `Vec<CryptoHash>` (not a set), so duplicates are structurally admitted. [4](#0-3) 

---

### Impact Explanation

Any `deposit` carried by the stuck `ActionReceipt` is permanently unrecoverable — it is neither executed nor refunded. This satisfies the **loss of funds** and **balance manipulation** impact criteria. The broken invariant is: *every `ActionReceipt` whose `PendingDataCount` reaches 0 must eventually execute*. With inflated `PendingDataCount`, that invariant is violated and the receipt is orphaned in trie state.

---

### Likelihood Explanation

A user deploys a contract that calls `promise_and` (or equivalent host functions) passing the same promise index twice, producing an `ActionReceipt` with `input_data_ids = [X, X]`. Deploying and calling contracts is an ordinary, unprivileged user action on NEAR. No validator or admin cooperation is required. The `input_data_ids` field carries no runtime deduplication guard between contract execution and receipt storage.

---

### Recommendation

In `process_action_receipt`, deduplicate `input_data_ids` before computing `pending_data_count`:

```rust
let unique_data_ids: HashSet<_> = action_receipt.input_data_ids().iter().collect();
let mut pending_data_count: u32 = 0;
for data_id in &unique_data_ids {
    if !has_received_data(state_update, account_id, **data_id)? {
        pending_data_count += 1;
        set(state_update, TrieKey::PostponedReceiptId { ... }, receipt.receipt_id());
    }
}
```

Alternatively, add a uniqueness check to `validate_receipt` / `validate_actions_with_mode` that rejects any `ActionReceipt` whose `input_data_ids` contains repeated entries, analogous to the `DelegateActionMustBeOnlyOne` guard already present for duplicate `Delegate` actions. [5](#0-4) 

---

### Proof of Concept

```
1. Alice deploys a contract `dup_promise.wasm` whose exported function does:
      let p = promise_create("bob.near", "noop", b"", 0, 5_000_000_000_000);
      let combined = promise_and(&[p, p]);   // same index twice
      promise_then(combined, "alice.near", "callback", b"", deposit=10_NEAR, gas=5TGas);

2. Alice calls `dup_promise.noop` via a signed transaction.

3. Runtime executes the function call and emits an ActionReceipt R for alice.near with:
      input_data_ids = [data_id_for_p, data_id_for_p]   // duplicate
      deposit = 10 NEAR

4. process_action_receipt sets PendingDataCount[R] = 2,
   PostponedReceiptId[data_id_for_p] = R.

5. The DataReceipt for data_id_for_p arrives.
   process_receipt:
     - removes PostponedReceiptId[data_id_for_p]
     - decrements PendingDataCount[R]: 2 → 1
     - does NOT execute R (count ≠ 0)

6. No second DataReceipt for data_id_for_p will ever arrive.
   PendingDataCount[R] = 1 forever.
   R is permanently postponed; the 10 NEAR deposit is permanently lost.
``` [6](#0-5)

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

**File:** runtime/runtime/src/action_validation.rs (L62-125)
```rust
pub(crate) fn validate_actions_with_mode(
    limit_config: &LimitConfig,
    actions: &[Action],
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    if actions.len() as u64 > limit_config.max_actions_per_receipt {
        return Err(ActionsValidationError::TotalNumberOfActionsExceeded {
            total_number_of_actions: actions.len() as u64,
            limit: limit_config.max_actions_per_receipt,
        });
    }

    // Centralized post-quantum gate. Mirrors the tx-admission gate in
    // `check_valid_for_config`, and is load-bearing for actions emitted by
    // contracts via host functions: those actions create new receipts that
    // never go through tx admission, so on a pre-feature protocol they must
    // be rejected here. The exhaustive match in
    // `Action::post_quantum_signatures_required` (including the recursive
    // walk into `Delegate`) forces every future action variant to make an
    // explicit decision at compile time.
    if !ProtocolFeature::PostQuantumSignatures.enabled(current_protocol_version)
        && actions.iter().any(Action::post_quantum_signatures_required)
    {
        return Err(ActionsValidationError::UnsupportedProtocolFeature {
            protocol_feature: "PostQuantumSignatures".to_owned(),
            version: current_protocol_version,
        });
    }

    if mode == ValidateReceiptMode::NewReceipt {
        validate_number_of_deploy_actions(actions, limit_config.max_deploy_actions_per_receipt)?;
    }

    let mut found_delegate_action = false;
    let mut iter = actions.iter().peekable();
    while let Some(action) = iter.next() {
        if let Action::DeleteAccount(_) = action {
            if iter.peek().is_some() {
                return Err(ActionsValidationError::DeleteActionMustBeFinal);
            }
        } else {
            if let Action::Delegate(_) | Action::DelegateV2(_) = action {
                if found_delegate_action {
                    return Err(ActionsValidationError::DelegateActionMustBeOnlyOne);
                }
                found_delegate_action = true;
            }
        }
        validate_action_with_mode(limit_config, action, receiver, current_protocol_version, mode)?;
    }

    let total_prepaid_gas =
        total_prepaid_gas(actions).map_err(|_| ActionsValidationError::IntegerOverflow)?;
    if total_prepaid_gas > limit_config.max_total_prepaid_gas {
        return Err(ActionsValidationError::TotalPrepaidGasExceeded {
            total_prepaid_gas,
            limit: limit_config.max_total_prepaid_gas,
        });
    }

    Ok(())
}
```

**File:** core/primitives/src/receipt.rs (L609-641)
```rust
/// ActionReceipt is derived from a set of Actions from `Transaction or from Receipt`
#[derive(
    BorshSerialize,
    BorshDeserialize,
    Debug,
    PartialEq,
    Eq,
    Clone,
    serde::Serialize,
    serde::Deserialize,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct ActionReceiptV2 {
    /// A signer of the original transaction
    pub signer_id: AccountId,
    /// The receiver of any balance refunds form this receipt if it is different from receiver_id.
    pub refund_to: Option<AccountId>,
    /// An access key which was used to sign the original transaction
    pub signer_public_key: PublicKey,
    /// A gas_price which has been used to buy gas in the original transaction
    pub gas_price: Balance,
    /// If present, where to route the output data
    pub output_data_receivers: Vec<DataReceiver>,
    /// A list of the input data dependencies for this Receipt to process.
    /// If all `input_data_ids` for this receipt are delivered to the account
    /// that means we have all the `ReceivedData` input which will be than converted to a
    /// `PromiseResult::Successful(value)` or `PromiseResult::Failed`
    /// depending on `ReceivedData` is `Some(_)` or `None`
    pub input_data_ids: Vec<CryptoHash>,
    /// A list of actions to process when all input_data_ids are filled
    pub actions: Vec<Action>,
}
```
