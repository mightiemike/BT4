### Title
Unbounded PromiseYield Timeout Queue Processing Without Compute Accounting — (`runtime/runtime/src/lib.rs`)

### Summary

`resolve_promise_yield_timeouts` iterates the entire expired `PromiseYieldTimeout` queue in a single chunk application without ever incrementing `total.compute`. The compute-limit guard at the top of the loop is therefore a no-op for work done inside this function. An unprivileged user can pre-fill the queue by creating many yields in one block, then let them expire simultaneously, forcing a chunk to perform unbounded trie work beyond the compute budget.

### Finding Description

`resolve_promise_yield_timeouts` is the last step of `process_receipts` in `runtime/runtime/src/lib.rs`:

```rust
while promise_yield_indices.first_index < promise_yield_indices.next_available_index {
    if total.compute >= compute_limit
        || state_update.trie.check_proof_size_limit_exceed()
    {
        break;
    }
    // trie read: queue entry
    // trie read: PromiseYieldReceipt existence check
    // trie read: yield receipt body (if save_receipt_to_tx)
    // trie write: forward_or_buffer_receipt → outgoing buffer
    // trie remove: queue entry
    promise_yield_indices.first_index += 1;
}
``` [1](#0-0) 

`total.compute` is **never incremented** inside this loop. The guard checks the value accumulated by the preceding receipt-processing phases (`process_local_receipts`, `process_delayed_receipts`, `process_incoming_receipts`). If those phases consumed little compute (e.g., the chunk is otherwise empty), `total.compute` stays at 0 and the guard never fires, so every expired timeout is processed regardless of how many there are.

Each iteration performs 3–5 trie operations (reads + writes) that are uncharged. The only real backstop is `check_proof_size_limit_exceed()`, which is checked once per iteration, not after each individual trie operation within an iteration. [2](#0-1) 

The `PromiseYieldTimeout` queue is populated by `enqueue_promise_yield_timeout` whenever a contract calls `promise_yield_create`. All yields created in block *B* receive `expires_at = B + yield_timeout_length_in_blocks` (200 blocks by protocol config). [3](#0-2) 

`PromiseYieldIndices` is a persistent trie queue with no per-chunk cap on how many entries may expire simultaneously. [4](#0-3) 

### Impact Explanation

An attacker who creates *N* yields in block *B* (paying gas at creation time) causes the chunk at block *B+200* to perform O(N) uncharged trie operations in `resolve_promise_yield_timeouts`. If the attacker also ensures the expiry chunk is otherwise lightly loaded (so `total.compute` is near zero when the function is called), all *N* timeouts are processed in a single chunk application with no compute budget enforcement.

This breaks the invariant that chunk execution time is bounded by `compute_limit`. Validators processing the expiry chunk spend more wall-clock time than the protocol budget allows, which can delay chunk production and, under sustained attack, cause missed chunks and degraded finality. This is a non-network-level denial of service reachable from ordinary user transactions.

### Likelihood Explanation

- `promise_yield_create` is a standard host function available to any deployed contract.
- `yield_timeout_length_in_blocks = 200` gives the attacker a predictable expiry block.
- The attacker pays gas only at creation time; the uncharged work at expiry is proportional to the number of yields created.
- No privileged role or validator access is required.
- The attack is repeatable every 200 blocks.

### Recommendation

Charge compute (or a dedicated per-timeout cost) inside `resolve_promise_yield_timeouts` for each iteration, mirroring how `process_delayed_receipts` breaks when `total.compute >= compute_limit` after each receipt is processed. Alternatively, introduce a per-chunk cap on the number of yield timeouts resolved (analogous to the recommendation in the external report to pass a `count` parameter), and carry unprocessed expired timeouts forward to the next chunk. [5](#0-4) 

### Proof of Concept

1. Deploy a contract that calls `promise_yield_create` in a loop (up to `max_promises_per_function_call_action = 1024` per call).
2. Submit enough transactions in block *B* to fill the block gas limit with yield-create calls, creating *N* yields all expiring at block *B + 200*.
3. Submit no further transactions for 200 blocks, keeping the expiry chunk lightly loaded.
4. At block *B + 200*, `resolve_promise_yield_timeouts` enters the while-loop with `total.compute ≈ 0`. It processes all *N* expired timeouts — each requiring 3–5 trie reads/writes — without any compute being charged or the loop breaking early.
5. The chunk's actual trie work far exceeds what `compute_limit` was designed to bound, extending chunk application time beyond the protocol's budget. [6](#0-5)

### Citations

**File:** runtime/runtime/src/lib.rs (L2418-2423)
```rust
        loop {
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                break;
            }
```

**File:** runtime/runtime/src/lib.rs (L2942-3049)
```rust
fn resolve_promise_yield_timeouts(
    processing_state: &mut ApplyProcessingReceiptState,
    receipt_sink: &mut ReceiptSink,
    compute_limit: u64,
) -> Result<ResolvePromiseYieldTimeoutsResult, RuntimeError> {
    let mut state_update = &mut processing_state.state_update;
    let total = &mut processing_state.total;
    let apply_state = &processing_state.apply_state;

    let mut promise_yield_indices: PromiseYieldIndices =
        get(state_update, &TrieKey::PromiseYieldIndices)?.unwrap_or_default();
    let initial_promise_yield_indices = promise_yield_indices.clone();
    let mut new_receipt_index: usize = 0;

    let mut processed_yield_timeouts = vec![];
    let yield_processing_start = std::time::Instant::now();
    while promise_yield_indices.first_index < promise_yield_indices.next_available_index {
        if total.compute >= compute_limit || state_update.trie.check_proof_size_limit_exceed() {
            break;
        }

        let queue_entry_key =
            TrieKey::PromiseYieldTimeout { index: promise_yield_indices.first_index };

        let queue_entry =
            get::<PromiseYieldTimeout>(state_update, &queue_entry_key)?.ok_or_else(|| {
                StorageError::StorageInconsistentState(format!(
                    "PromiseYield timeout queue entry #{} should be in the state",
                    promise_yield_indices.first_index
                ))
            })?;

        // Queue entries are ordered by expires_at
        if queue_entry.expires_at > apply_state.block_height {
            break;
        }

        // Check if the yielded promise still needs to be resolved
        let promise_yield_key = TrieKey::PromiseYieldReceipt {
            receiver_id: queue_entry.account_id.clone(),
            data_id: queue_entry.data_id,
        };
        if state_update.contains_key(&promise_yield_key, AccessOptions::DEFAULT)? {
            let new_receipt_id = create_receipt_id_from_receipt_id(
                &queue_entry.data_id,
                apply_state.block_height,
                new_receipt_index,
            );
            new_receipt_index += 1;

            // Create a PromiseResume receipt to resolve the timed-out yield.
            let resume_receipt = Receipt::V0(ReceiptV0 {
                predecessor_id: queue_entry.account_id.clone(),
                receiver_id: queue_entry.account_id.clone(),
                receipt_id: new_receipt_id,
                receipt: ReceiptEnum::PromiseResume(DataReceipt {
                    data_id: queue_entry.data_id,
                    data: None,
                }),
            });

            // Record a ReceiptToTx entry for the new resume receipt. The parent is the
            // yield receipt that is being timed out.
            if processing_state.apply_state.save_receipt_to_tx {
                let yield_receipt: Receipt = get_pure(state_update, &promise_yield_key)?
                    .expect("promise yield receipt should exist since contains_key was true");
                processing_state.receipt_to_tx.push((
                    new_receipt_id,
                    ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                        origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                            parent_receipt_id: *yield_receipt.receipt_id(),
                            parent_predecessor_id: yield_receipt.predecessor_id().clone(),
                        }),
                        receiver_account_id: queue_entry.account_id.clone(),
                        shard_id: processing_state.apply_state.shard_id,
                    }),
                ));
            }

            // The receipt is destined for the local shard and will be placed in the outgoing
            // receipts buffer. It is possible that there is already an outgoing receipt resolving
            // this yield if `yield_resume` was invoked by some receipt which was processed in
            // the current chunk. The ordering will be maintained because the receipts are
            // destined for the same shard; the timeout will be processed second and discarded.
            receipt_sink.forward_or_buffer_receipt(
                resume_receipt,
                apply_state,
                &mut state_update,
            )?;
        }

        processed_yield_timeouts.push(queue_entry);
        state_update.remove(queue_entry_key);
        // Math checked above: first_index is less than next_available_index
        promise_yield_indices.first_index += 1;
    }
    processing_state.metrics.yield_timeouts_done(
        processed_yield_timeouts.len() as u64,
        yield_processing_start.elapsed(),
        total.gas,
        total.compute,
    );
    Ok(ResolvePromiseYieldTimeoutsResult {
        initial_promise_yield_indices,
        promise_yield_indices,
        processed_yield_timeouts,
    })
}
```

**File:** core/store/src/utils/mod.rs (L164-180)
```rust
pub fn enqueue_promise_yield_timeout(
    state_update: &mut TrieUpdate,
    promise_yield_indices: &mut PromiseYieldIndices,
    account_id: AccountId,
    data_id: CryptoHash,
    expires_at: BlockHeight,
) {
    set(
        state_update,
        TrieKey::PromiseYieldTimeout { index: promise_yield_indices.next_available_index },
        &PromiseYieldTimeout { account_id, data_id, expires_at },
    );
    promise_yield_indices.next_available_index = promise_yield_indices
        .next_available_index
        .checked_add(1)
        .expect("Next available index for PromiseYield timeout queue exceeded the integer limit");
}
```

**File:** core/primitives/src/receipt.rs (L1067-1080)
```rust
/// Stores indices for a persistent queue for PromiseYield timeouts.
#[derive(Default, BorshSerialize, BorshDeserialize, Clone, PartialEq, Debug, ProtocolSchema)]
pub struct PromiseYieldIndices {
    // First inclusive index in the queue.
    pub first_index: u64,
    // Exclusive end index of the queue
    pub next_available_index: u64,
}

impl PromiseYieldIndices {
    pub fn len(&self) -> u64 {
        self.next_available_index - self.first_index
    }
}
```
