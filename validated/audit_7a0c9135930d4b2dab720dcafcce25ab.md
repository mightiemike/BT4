## Title
Duplicate `input_data_ids` in an `ActionReceipt` causes a double-remove of `ReceivedData`, triggering an unhandled `StorageInconsistentState` panic that halts every node - (File: `runtime/runtime/src/lib.rs`)

### Summary
`Runtime::apply_action_receipt` collects an `ActionReceipt`'s `PromiseResult`s by iterating `action_receipt.input_data_ids()`, fetching each id's `ReceivedData` from the trie and then removing it. If the same `data_id` appears more than once in `input_data_ids`, the second lookup finds nothing (it was already removed by the first), and the runtime treats this as an unrecoverable storage corruption (`StorageError::StorageInconsistentState`). At the call-site in `chain/chain/src/runtime/mod.rs`, any `StorageInconsistentState` that isn't `FlatStorageBlockNotSupported`/`MissingTrieValue` is turned into `panic!`, crashing the validator/RPC process applying the chunk. This mirrors the CKB advisory's pattern: referencing a dependency ("DepGroup"/data id) that is not actually alive at the time of use crashes the receiving node.

### Finding Description
`apply_action_receipt` builds the `promise_results` array like this: [1](#0-0) 

For every entry in `input_data_ids()` it calls `get_received_data(...)` and immediately `state_update.remove(TrieKey::ReceivedData{...})`. If `input_data_ids` contains the same `data_id` twice, the first iteration consumes and deletes the `ReceivedData` entry; the second iteration's `get_received_data` call returns `None`, hitting the `.ok_or_else(...)` branch which returns: [2](#0-1) 

This is only reached when `process_action_receipt` determined the receipt is fully ready to execute — i.e. `has_received_data` returned `true` for *every* (non-deduplicated) entry in `input_data_ids`, including duplicates, before scheduling immediate execution: [3](#0-2) 

Nothing in `process_action_receipt`, `validate_action_receipt`, or `ReceiptManager::create_action_receipt` rejects or deduplicates repeated `data_id`s in `input_data_ids`: [4](#0-3) [5](#0-4) 

The resulting `RuntimeError::StorageError(StorageError::StorageInconsistentState(..))` is not handled gracefully; the top-level `apply_chunk` wrapper explicitly panics on any such error that is not `FlatStorageBlockNotSupported`/`MissingTrieValue`: [6](#0-5) 

A comment in the test suite for a related trie-corruption case confirms this is a known, deliberate hard-panic behavior in production: [7](#0-6) 

### Impact Explanation
Any chunk producer/validator/RPC node that applies a receipt whose `input_data_ids` list contains a duplicate `data_id` (with that data already/eventually delivered) will panic mid-`apply_chunk`, crashing the process. Because block/chunk application is deterministic and all honest nodes execute the same receipts, this is a transaction-triggered chain halt: every validator tracking the shard crashes on the same input, which is a Critical availability impact consistent with "Process crashes when the cell used as DepGroup is not alive" in the CKB advisory (referencing a dependency that is no longer available/consistent crashes the node instead of failing the single transaction).

### Likelihood Explanation
The reachable question is whether an unprivileged account can actually construct an `ActionReceipt` with a duplicated `data_id` in `input_data_ids`. This is not fully proven from static reading alone: `input_data_ids` for cross-contract-call receipts are normally populated by the runtime itself (one fresh, unique `data_id` per dependency, generated when a callback receipt is created via `promise_and`/`promise_batch_then` in `ReceiptManager::create_action_receipt`). I was not able to find, within the available index, the exact code path that generates each `data_id` (`ext.rs`/`dependencies.rs` matches were returned but not inspected in full) to confirm or rule out whether a contract can force the *same* generated `data_id` to be attached twice to one receipt's `input_data_ids` (e.g., via repeated `promise_and` on the same promise index, or via `yield_create`/`yield_resume` id reuse). If such a path exists, the likelihood is high, since it requires only a single crafted `FunctionCall` from any account — no special privileges, staking, or validator access. If no such path exists, this analog does not hold and should be treated as unconfirmed.

### Recommendation
- Reject `ActionReceipt`s (and `ActionReceiptV2`) whose `input_data_ids` contain duplicate entries during `validate_receipt`/`validate_action_receipt`, independent of whether the runtime-generated path can currently produce them, as defense-in-depth.
- In `apply_action_receipt`, make the collection of `promise_results` resilient to duplicate ids (e.g., collect distinct ids first, or track already-consumed ids) instead of treating a second lookup as `StorageInconsistentState`.
- Audit all call sites that append to `input_data_ids` (`ReceiptManager::create_action_receipt`, yield/resume id generation) to confirm whether the same `data_id` can currently be attached more than once to a single receipt, and add an explicit assertion/rejection if the invariant is only enforced by convention.

### Proof of Concept
Not independently verified end-to-end (would require confirming the exact host-function path that can produce a duplicated `data_id` within one receipt's `input_data_ids`, which the available index did not fully expose). Conceptual PoC, contingent on that path existing:
1. From an unprivileged account, submit a `FunctionCall` that creates two dependent promises using `promise_and`/`promise_batch_then` in a way that reuses the same underlying promise/data dependency twice for a single callback receipt, so the generated `input_data_ids` vector contains the same `CryptoHash` twice.
2. Let the corresponding `DataReceipt` be delivered normally.
3. When the callback `ActionReceipt` executes, `apply_action_receipt`'s second consumption of the duplicated `data_id` finds the `ReceivedData` already removed, returns `StorageError::StorageInconsistentState`, and `chain/chain/src/runtime/mod.rs`'s `apply_chunk` panics, crashing every node applying that chunk.

### Citations

**File:** runtime/runtime/src/lib.rs (L870-898)
```rust
        } else {
            action_receipt
                .input_data_ids()
                .iter()
                .map(|data_id| {
                    let ReceivedData { data } =
                        get_received_data(state_update, account_id, *data_id)?.ok_or_else(
                            || {
                                StorageError::StorageInconsistentState(
                                    "received data should be in the state".to_string(),
                                )
                            },
                        )?;
                    state_update.remove(TrieKey::ReceivedData {
                        receiver_id: account_id.clone(),
                        data_id: *data_id,
                    });
                    match data {
                        // TODO: Going from Vec<u8> to Rc<[u8]> shrinks the
                        // allocated buffer to fit, which may re-allocate if the
                        // capacity > len.
                        // Most likely, capacity == len holds here anyway but it
                        // would be better to use `Rc<u8>` already in `ReceivedData`
                        // and `DataReceipt`.
                        Some(value) => Ok(PromiseResult::Successful(Rc::from(value))),
                        None => Ok(PromiseResult::Failed),
                    }
                })
                .collect::<Result<Arc<[PromiseResult]>, RuntimeError>>()?
```

**File:** runtime/runtime/src/lib.rs (L1662-1696)
```rust
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
```

**File:** runtime/runtime/src/verifier.rs (L742-769)
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
```

**File:** runtime/runtime/src/receipt_manager.rs (L112-138)
```rust
    pub(super) fn create_action_receipt(
        &mut self,
        input_data_ids: Vec<CryptoHash>,
        receipt_indices: Vec<ReceiptIndex>,
        receiver_id: AccountId,
    ) -> Result<ReceiptIndex, VMLogicError> {
        assert_eq!(input_data_ids.len(), receipt_indices.len());
        for (data_id, receipt_index) in input_data_ids.iter().zip(receipt_indices.into_iter()) {
            self.action_receipts
                .get_mut(receipt_index as usize)
                .ok_or(HostError::InvalidReceiptIndex { receipt_index })?
                .output_data_receivers
                .push(DataReceiver { data_id: *data_id, receiver_id: receiver_id.clone() });
        }

        let new_receipt = ActionReceiptMetadata {
            receiver_id,
            refund_to: None,
            output_data_receivers: vec![],
            input_data_ids,
            actions: vec![],
            is_promise_yield: false,
        };
        let new_receipt_index = self.action_receipts.len() as ReceiptIndex;
        self.action_receipts.push(new_receipt);
        Ok(new_receipt_index)
    }
```

**File:** chain/chain/src/runtime/mod.rs (L1286-1304)
```rust
        match self.process_state_update(
            trie,
            apply_reason,
            chunk,
            block,
            receipts,
            transactions,
            storage_config.state_patch,
        ) {
            Ok(result) => Ok(result),
            Err(e) => match e {
                Error::StorageError(err) => match &err {
                    StorageError::FlatStorageBlockNotSupported(_)
                    | StorageError::MissingTrieValue(..) => Err(err.into()),
                    _ => panic!("{err}"),
                },
                _ => Err(e),
            },
        }
```

**File:** core/store/src/trie/shard_tries.rs (L1199-1204)
```rust
    /// Holds a `Trie` for the parent shard, pauses between two reads while
    /// the main thread runs `freeze_parent_memtrie`, and verifies that the
    /// second read still succeeds. Without preserving the frozen data in the
    /// old parent Arc, the second read would hit an empty MemTries and fail
    /// with `StorageInconsistentState` (panicking in production via the
    /// catch-all in `chain/chain/src/runtime/mod.rs`).
```
