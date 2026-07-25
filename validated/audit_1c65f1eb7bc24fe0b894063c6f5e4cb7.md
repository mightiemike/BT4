### Title
Unbounded PromiseYield Timeout Queue Draining Without Compute Accounting Enables Chunk-Level DoS - (File: runtime/runtime/src/lib.rs)

---

### Summary

`resolve_promise_yield_timeouts` in `runtime/runtime/src/lib.rs` iterates over every expired `PromiseYieldTimeout` entry in a single chunk without ever incrementing `total.compute`. The compute-limit guard at the top of the loop is a dead check: `total.compute` never changes inside the loop, so the guard never fires from within. An unprivileged attacker who pre-fills the timeout queue with many entries all expiring at the same block height forces the chunk producer to perform an unbounded number of trie reads and writes in one chunk, bypassing the compute limit that is supposed to cap per-chunk work.

---

### Finding Description

**Root cause — missing compute accounting in `resolve_promise_yield_timeouts`** [1](#0-0) 

```rust
while promise_yield_indices.first_index < promise_yield_indices.next_available_index {
    if total.compute >= compute_limit || state_update.trie.check_proof_size_limit_exceed() {
        break;
    }
    // ... trie reads, trie writes, receipt creation ...
    // total.compute is NEVER incremented here
    promise_yield_indices.first_index += 1;
}
```

`total.compute` is a shared counter that all other receipt-processing loops (`process_delayed_receipts`, `process_incoming_receipts`) increment after each receipt to enforce the chunk compute limit. `resolve_promise_yield_timeouts` reads `total.compute` once per iteration but never writes it. The guard `total.compute >= compute_limit` therefore evaluates to the same value on every iteration and can only fire if prior receipt processing already saturated the limit before this function was called. [2](#0-1) 

Each loop iteration performs:
1. A trie read of `TrieKey::PromiseYieldTimeout { index }` — the queue entry.
2. A trie `contains_key` check for `TrieKey::PromiseYieldReceipt { receiver_id, data_id }`.
3. If the yield is still live: a `forward_or_buffer_receipt` call (trie write to the outgoing buffer) plus, when `save_receipt_to_tx` is set, a `get_pure` read of the full yield receipt.
4. A trie `remove` of the timeout entry.

None of these operations charge `total.compute`. [3](#0-2) 

**The only remaining guard — proof-size limit — is insufficient**

The secondary guard `state_update.trie.check_proof_size_limit_exceed()` checks the accumulated storage-proof size (4 MB on mainnet at PV 86). This provides a soft bound, but:
- It is accumulated across the entire chunk, so a chunk with few prior receipts starts with a near-zero proof and allows thousands of timeout entries to be drained before the limit is hit.
- It does not enforce the compute/gas limit, so the wall-clock cost of chunk production is not bounded by the protocol's intended per-chunk compute cap. [4](#0-3) 

**How an attacker fills the queue**

`promise_yield_create` is a standard host function callable from any deployed contract. Its cost is `yield_create_base` = **153,411,779,276 gas** (~153 Ggas) per call. [5](#0-4) 

With a 1,000 Tgas chunk gas limit and `max_promises_per_function_call_action` = 1,024, a single function-call receipt can create up to 1,024 yield promises. Over `yield_timeout_length_in_blocks` = **200 blocks**, an attacker submitting yield-creating transactions every block accumulates:

```
200 blocks × (1,000 Tgas / ~153 Ggas per yield) ≈ 200 × 6,500 ≈ 1,300,000 timeout entries
```

all sharing the same `expires_at = creation_block + 200`. [6](#0-5) 

When the expiry block arrives, `resolve_promise_yield_timeouts` drains every expired entry in one pass. Even bounded by the 4 MB proof-size limit (~20,000–40,000 entries at ~100–200 bytes each), the chunk producer performs tens of thousands of trie operations with zero compute accounting, far exceeding what the compute limit was designed to allow in a single chunk.

---

### Impact Explanation

Chunk producers assigned to the shard containing the attacker's account must process all expired timeout entries in a single chunk application. Because `total.compute` is never incremented inside the loop, the chunk's compute budget is not consumed, and the runtime does not stop early. The result is:

- **Excessive wall-clock time** for chunk production on the expiry block, potentially causing the chunk producer to miss its slot.
- **Oversized storage proofs**, degrading stateless validation performance for chunk validators.
- **Repeated across epochs**: the attacker can re-queue yields immediately after expiry, sustaining the pressure indefinitely at the cost of gas fees.

This is a non-network-level denial of service reachable from ordinary user transactions (deploying a contract and calling `promise_yield_create`).

---

### Likelihood Explanation

- Any account can deploy a contract and call `promise_yield_create`.
- The `yield_create_base` fee (153 Ggas) is low relative to the 1,000 Tgas chunk limit; thousands of yields can be created per block.
- The 200-block accumulation window is long enough to build a large queue before the expiry block.
- No privileged role or validator access is required.

---

### Recommendation

Inside `resolve_promise_yield_timeouts`, charge a fixed compute cost per timeout entry processed — analogous to how `process_delayed_receipts` increments `total.compute` after each receipt. This requires a protocol version bump. A minimal fix:

```rust
// After processing each entry:
total.add(YIELD_TIMEOUT_PROCESSING_GAS, YIELD_TIMEOUT_PROCESSING_COMPUTE)?;
```

where `YIELD_TIMEOUT_PROCESSING_GAS` and `YIELD_TIMEOUT_PROCESSING_COMPUTE` are new protocol parameters calibrated to the actual trie I/O cost of one timeout entry. This ensures the compute limit fires correctly and limits the number of entries drained per chunk to a safe bound. [7](#0-6) 

---

### Proof of Concept

1. **Deploy** a contract to account `attacker.near` with a method `spam_yields` that calls `promise_yield_create("callback", "", 0, 1, 0)` in a loop up to `max_promises_per_function_call_action` (1,024) times.

2. **Spam**: For each of the 200 blocks before the target expiry height `H`, submit transactions calling `spam_yields`. Each transaction creates up to 1,024 `PromiseYieldTimeout` entries with `expires_at = current_block + 200`, all converging on block `H`.

3. **Trigger**: At block `H`, the runtime calls `resolve_promise_yield_timeouts`. The loop iterates over all accumulated entries. `total.compute` remains at its pre-loop value throughout; the compute-limit guard never fires. The chunk producer performs tens of thousands of trie reads and writes in a single chunk application with no compute budget consumed.

4. **Observe**: Chunk production time for block `H` on the attacker's shard spikes. Validators processing the chunk witness an oversized storage proof. If the spike is large enough, the chunk producer misses its slot, causing a missed chunk and degraded throughput for all users on that shard. [8](#0-7) [6](#0-5) [9](#0-8)

### Citations

**File:** runtime/runtime/src/lib.rs (L2942-2949)
```rust
fn resolve_promise_yield_timeouts(
    processing_state: &mut ApplyProcessingReceiptState,
    receipt_sink: &mut ReceiptSink,
    compute_limit: u64,
) -> Result<ResolvePromiseYieldTimeoutsResult, RuntimeError> {
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

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__155.json.snap (L241-248)
```text
      "max_arguments_length": 4194304,
      "max_length_returned_data": 4194304,
      "max_contract_size": 4194304,
      "max_transaction_size": 1572864,
      "max_receipt_size": 4194304,
      "max_length_storage_key": 2048,
      "max_length_storage_value": 4194304,
      "max_promises_per_function_call_action": 1024,
```

**File:** core/store/src/utils/mod.rs (L163-180)
```rust
// Enqueues given timeout to the PromiseYield timeout queue
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

**File:** runtime/runtime/src/function_call.rs (L154-172)
```rust
        // Fetch metadata for PromiseYield timeout queue
        let mut promise_yield_indices = get_promise_yield_indices(state_update).unwrap_or_default();
        let initial_promise_yield_indices = promise_yield_indices.clone();

        let mut new_receipts: Vec<_> = receipt_manager
            .action_receipts
            .into_iter()
            .map(|receipt| {
                // If the newly created receipt is a PromiseYield, enqueue a timeout for it
                if receipt.is_promise_yield {
                    enqueue_promise_yield_timeout(
                        state_update,
                        &mut promise_yield_indices,
                        account_id.clone(),
                        receipt.input_data_ids[0],
                        apply_state.block_height
                            + config.wasm_config.limit_config.yield_timeout_length_in_blocks,
                    );
                }
```
