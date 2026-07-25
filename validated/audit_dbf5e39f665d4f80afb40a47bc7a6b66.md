### Title
`resolve_promise_yield_timeouts` Iterates All Expired Yields Without Updating Compute Counter, Bypassing Chunk Gas Limit — (`runtime/runtime/src/lib.rs`)

### Summary

`resolve_promise_yield_timeouts` is called at the end of every chunk's `process_receipts`. It walks the `PromiseYieldTimeout` queue and fires a `PromiseResume` receipt for every entry whose `expires_at <= block_height`. The loop guards itself with `total.compute >= compute_limit`, but **`total.compute` is never updated inside the loop**. If the chunk arrives at this step with any remaining compute budget, every expired timeout in the queue is processed in a single chunk with zero compute accounting — an exact structural analog to the Hubble `settleFunding` / `_calcTwap` unbounded-iteration pattern.

### Finding Description

`resolve_promise_yield_timeouts` at `runtime/runtime/src/lib.rs:2942`:

```rust
while promise_yield_indices.first_index < promise_yield_indices.next_available_index {
    if total.compute >= compute_limit          // ← checked once per iteration …
        || state_update.trie.check_proof_size_limit_exceed()
    {
        break;
    }
    // … trie reads, PromiseResume receipt creation, forward_or_buffer_receipt, trie removes …
    // ← total.compute is NEVER written here
    promise_yield_indices.first_index += 1;
}
``` [1](#0-0) 

`total` is a `&mut TotalResourceGuard` whose `.compute` field is the shared chunk-level compute accumulator. Every other receipt-processing loop (`process_local_receipts`, `process_delayed_receipts`, `process_incoming_receipts`) updates this counter via `process_receipt_with_metrics` and breaks when the limit is reached. `resolve_promise_yield_timeouts` reads the counter but never writes it, so the guard condition is a no-op for the entire duration of the timeout sweep. [2](#0-1) 

The only real termination condition is the secondary `check_proof_size_limit_exceed()` guard, which fires when the storage-proof buffer overflows — a limit that is independent of the gas/compute budget and is not enforced uniformly across all chunk work. [3](#0-2) 

Each loop iteration performs:
- Two trie reads (`PromiseYieldTimeout` queue entry + `PromiseYieldReceipt` existence check)
- One `forward_or_buffer_receipt` call (trie write + congestion-control bookkeeping)
- One trie remove (`state_update.remove(queue_entry_key)`) [4](#0-3) 

None of this work is charged to `total.compute`.

### Impact Explanation

An unprivileged user can accumulate a large number of `PromiseYield` receipts — each paid for with normal gas — all sharing the same `expires_at` block height (determined by `yield_timeout_length_in_blocks`). When that block is applied, `resolve_promise_yield_timeouts` sweeps the entire expired cohort in one pass, performing O(N) unbounded trie I/O outside the compute budget. This:

1. **Extends chunk application time** beyond what the gas/compute limit is supposed to guarantee, degrading block production latency for honest validators.
2. **Floods the outgoing receipt buffer** with N `PromiseResume` receipts simultaneously, creating a receipt backlog that congestion control must absorb over subsequent blocks.
3. **Breaks the invariant** that `compute_limit` bounds the work done per chunk — the same invariant the Hubble `settleFunding` bug broke against the Avalanche block gas limit.

The impact is non-network-level (it affects chunk execution time and receipt throughput, not peer connectivity) and is fixable without a hardfork by charging `total.compute` inside the loop.

### Likelihood Explanation

- Any account can call `promise_yield_create` (or `promise_yield_create_with_id`) from a deployed contract; no privileged role is required.
- The attacker pays normal gas to create yields. The timeout length is a fixed protocol constant (`yield_timeout_length_in_blocks`), so all yields created in the same block expire together.
- The attack scales with the attacker's gas budget: more gas → more yields → larger unaccounted sweep at expiry.
- The secondary `check_proof_size_limit_exceed()` bound limits the sweep to however many trie nodes fit in the proof budget, but this is a separate, larger limit that is not the intended compute guard.

### Recommendation

Inside `resolve_promise_yield_timeouts`, after each timeout is processed, charge the work to the shared compute accumulator:

```rust
// After processing each timeout entry:
total.add(0, per_timeout_compute_cost)?;
```

Alternatively, reuse `process_receipt_with_metrics` for the generated `PromiseResume` receipts so they are subject to the same compute accounting as all other receipts, and break the loop when `total.compute >= compute_limit` after the update.

### Proof of Concept

1. Deploy a contract that calls `promise_yield_create` in a loop (up to `max_number_of_promises` per call).
2. Submit many such transactions across multiple blocks, all timed so yields expire at block height `H`.
3. At block `H`, submit a single cheap transaction to ensure `total.compute < compute_limit` when `resolve_promise_yield_timeouts` runs.
4. Observe that `resolve_promise_yield_timeouts` sweeps all N expired entries in one chunk, performing O(N) trie operations with zero compute accounting, extending chunk application time proportionally to N.

The relevant loop with the missing compute update: [5](#0-4) 

The caller in `process_receipts` that passes `compute_limit` but receives no updated `total.compute` back from the sweep: [6](#0-5) 

The `is_instant_receipt` classification confirming `PromiseYield` receipts are the primary source of timeout queue entries: [7](#0-6)

### Citations

**File:** runtime/runtime/src/lib.rs (L2650-2652)
```rust
        // Resolve timed-out PromiseYield receipts
        let promise_yield_result =
            resolve_promise_yield_timeouts(processing_state, receipt_sink, compute_limit)?;
```

**File:** runtime/runtime/src/lib.rs (L2947-2949)
```rust
    let mut state_update = &mut processing_state.state_update;
    let total = &mut processing_state.total;
    let apply_state = &processing_state.apply_state;
```

**File:** runtime/runtime/src/lib.rs (L2958-3037)
```rust
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
```

**File:** core/primitives/src/receipt.rs (L473-479)
```rust
    pub fn is_instant_receipt(&self) -> bool {
        match self.versioned_receipt() {
            VersionedReceiptEnum::PromiseYield(_) => {
                // PromiseYield receipts are instant receipts.
                // Applying a PromiseYield receipt is one trie write, it's okay to make it an instant receipt.
                true
            }
```
