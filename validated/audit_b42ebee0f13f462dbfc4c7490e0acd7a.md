### Title
`resolve_promise_yield_timeouts` iterates expired yield queue without charging compute, enabling unbounded work beyond the gas budget - (File: `runtime/runtime/src/lib.rs`)

### Summary

`resolve_promise_yield_timeouts` is called unconditionally at the end of every chunk's receipt processing. It iterates over all expired `PromiseYieldTimeout` queue entries, performing trie reads, receipt construction, and receipt forwarding for each entry. Critically, the loop checks `total.compute >= compute_limit` but **never updates `total.compute` within the loop body**. This means the compute-limit guard is checking gas consumed by prior receipt processing, not by the timeout sweep itself. An unprivileged user can accumulate many yields expiring at the same block height and force the runtime to perform unbounded trie work that is not charged to the gas budget.

---

### Finding Description

In `Runtime::apply`, after processing local, delayed, and incoming receipts, the runtime calls `resolve_promise_yield_timeouts`:

```
// Resolve timed-out PromiseYield receipts
let promise_yield_result =
    resolve_promise_yield_timeouts(processing_state, receipt_sink, compute_limit)?;
``` [1](#0-0) 

Inside `resolve_promise_yield_timeouts`, the loop guard is:

```rust
while promise_yield_indices.first_index < promise_yield_indices.next_available_index {
    if total.compute >= compute_limit || state_update.trie.check_proof_size_limit_exceed() {
        break;
    }
    // ... trie reads, receipt construction, forward_or_buffer_receipt ...
    promise_yield_indices.first_index += 1;
}
``` [2](#0-1) 

`total` is a `&mut TotalResourceGuard` whose `compute` field is only incremented by `TotalResourceGuard::add`, which is called exclusively from `process_receipt_with_metrics`. **No call to `total.add` exists anywhere inside `resolve_promise_yield_timeouts`.** [3](#0-2) 

Consequently:

- If prior receipt processing consumed zero or little compute (e.g., an empty chunk), `total.compute` remains 0 throughout the entire timeout sweep.
- The loop iterates over every expired entry, performing at least two trie reads (`get::<PromiseYieldTimeout>` and `contains_key` for `PromiseYieldReceipt`), plus a `state_update.remove`, plus `forward_or_buffer_receipt` (which itself reads and writes buffered receipt state), for each entry.
- None of this work is charged to `total.compute`.

The only practical bound is `check_proof_size_limit_exceed()`, which accumulates trie witness bytes. With `per_receipt_storage_proof_size_limit` = 4 MB in production and each `PromiseYieldTimeout` entry being on the order of ~100 bytes, the loop can process tens of thousands of entries before the proof-size guard fires. [4](#0-3) 

Each `promise_yield_create` host function call enqueues one entry into the global per-shard `PromiseYieldTimeout` queue:

```rust
enqueue_promise_yield_timeout(
    state_update,
    &mut promise_yield_indices,
    account_id.clone(),
    receipt.input_data_ids[0],
    apply_state.block_height
        + config.wasm_config.limit_config.yield_timeout_length_in_blocks,
);
``` [5](#0-4) 

`yield_timeout_length_in_blocks` is 200 in production. All yields created in the same block expire at the same block height (current + 200), so a single block of attacker activity produces a single-block expiry spike 200 blocks later. [6](#0-5) 

---

### Impact Explanation

At the expiry block, `resolve_promise_yield_timeouts` sweeps the entire expired prefix of the queue in one pass, doing real trie I/O for each entry, without any gas/compute being charged. This work is invisible to the gas budget. Validators must complete this sweep before they can finalize the chunk and sign the block. If the sweep is large enough, it extends wall-clock block processing time beyond the expected budget, causing validators to miss their block production slot or fall behind on chunk endorsements. This is a non-network-level denial of service reachable from ordinary user transactions (any account that can call a contract using `promise_yield_create`).

---

### Likelihood Explanation

`max_promises_per_function_call_action` is 1024, meaning a single function call can enqueue up to 1024 timeout entries. [7](#0-6) 

Multiple function calls in the same block (each up to the 1000 TGas receipt gas limit) can enqueue thousands of entries all expiring at block N+200. The attacker pays gas proportional to the number of yields created, but the timeout-sweep work at block N+200 is uncharged. The ratio of attacker-paid gas to validator-imposed work grows with queue depth, making this economically attractive for a sustained griefing attack.

---

### Recommendation

Inside `resolve_promise_yield_timeouts`, charge a fixed compute/gas cost per iteration to `total` before processing each entry. This mirrors how `process_delayed_receipts` and `process_incoming_receipts` update `total` via `process_receipt_with_metrics` for every receipt they handle. A per-entry cost should be added to the fee schedule (e.g., `yield_timeout_base` execution fee) and deducted from `total.compute` at the top of the loop body, so the existing `total.compute >= compute_limit` guard becomes effective and the sweep is naturally bounded by the same gas budget as all other receipt processing.

---

### Proof of Concept

1. Deploy a contract with a method `flood_yields` that calls `promise_yield_create` 1024 times (the per-call promise limit) with a trivial callback.
2. Submit N transactions calling `flood_yields` in block B, all on the same shard. Each accepted receipt enqueues 1024 entries expiring at block B+200.
3. At block B+200, `resolve_promise_yield_timeouts` is called. `total.compute` entering the function reflects only the gas used by receipts processed earlier in that chunk. If the chunk is otherwise empty, `total.compute = 0`.
4. The loop iterates over all N×1024 expired entries. For each entry it performs: one `get::<PromiseYieldTimeout>` trie read, one `contains_key` trie read, one `state_update.remove`, and one `forward_or_buffer_receipt` call (which itself reads and writes buffered receipt trie state). `total.compute` is never incremented.
5. The loop only terminates when the accumulated trie witness exceeds `per_receipt_storage_proof_size_limit` (4 MB). With ~100-byte entries, this allows ~40,000 iterations of uncharged trie work per chunk.
6. Validators processing block B+200 spend significantly more wall-clock time than the gas budget implies, potentially missing their block production window. [8](#0-7) [9](#0-8)

### Citations

**File:** runtime/runtime/src/lib.rs (L2638-2640)
```rust
        // Resolve timed-out PromiseYield receipts
        let promise_yield_result =
            resolve_promise_yield_timeouts(processing_state, receipt_sink, compute_limit)?;
```

**File:** runtime/runtime/src/lib.rs (L2930-3025)
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
```

**File:** runtime/runtime/src/lib.rs (L3052-3057)
```rust
impl TotalResourceGuard {
    fn add(&mut self, gas: u64, compute: u64) -> Result<(), IntegerOverflowError> {
        self.gas = self.gas.checked_add(gas).ok_or(IntegerOverflowError)?;
        self.compute = safe_add_compute(self.compute, compute)?;
        Ok(())
    }
```

**File:** runtime/runtime/src/function_call.rs (L161-169)
```rust
                if receipt.is_promise_yield {
                    enqueue_promise_yield_timeout(
                        state_update,
                        &mut promise_yield_indices,
                        account_id.clone(),
                        receipt.input_data_ids[0],
                        apply_state.block_height
                            + config.wasm_config.limit_config.yield_timeout_length_in_blocks,
                    );
```

**File:** core/parameters/res/runtime_configs/parameters.yaml (L281-281)
```yaml
max_promises_per_function_call_action: 1_024
```

**File:** core/parameters/res/runtime_configs/parameters.yaml (L285-286)
```yaml
yield_timeout_length_in_blocks: 200
max_yield_payload_size: 1_024 # kiB
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L614-627)
```rust
    fn checked_push_promise(&mut self, promise: Promise) -> Result<PromiseIndex> {
        let new_promise_idx = self.promises.len() as PromiseIndex;
        self.promises.push(promise);
        if self.promises.len() as u64
            > self.config.limit_config.max_promises_per_function_call_action
        {
            Err(HostError::NumberPromisesExceeded {
                number_of_promises: self.promises.len() as u64,
                limit: self.config.limit_config.max_promises_per_function_call_action,
            }
            .into())
        } else {
            Ok(new_promise_idx)
        }
```
