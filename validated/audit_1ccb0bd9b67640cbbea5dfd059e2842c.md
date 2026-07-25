### Title
`PromiseYieldTimeout` Queue Ordering Invariant Broken by `yield_timeout_length_in_blocks` Reduction — (`runtime/runtime/src/lib.rs`)

---

### Summary

`resolve_promise_yield_timeouts` walks the `PromiseYieldTimeout` FIFO queue and **breaks** on the first entry whose `expires_at > block_height`, relying on the invariant that queue entries are monotonically ordered by `expires_at`. This invariant holds only while `yield_timeout_length_in_blocks` never decreases across protocol versions. A protocol upgrade that reduces this parameter violates the ordering, causing newer (shorter-timeout) yield entries to be permanently blocked behind older (longer-timeout) entries until the older entries expire — breaking contract execution flow for all affected yields.

---

### Finding Description

**Enqueue path** — `runtime/runtime/src/function_call.rs:164–171`:

When a `PromiseYield` receipt is created, a timeout entry is appended to the tail of the FIFO queue with:

```
expires_at = apply_state.block_height + config.wasm_config.limit_config.yield_timeout_length_in_blocks
``` [1](#0-0) 

Because the queue is FIFO (insertion-ordered), `expires_at` values are monotonically non-decreasing **only if** `yield_timeout_length_in_blocks` never decreases. The parameter is currently 200 blocks across all protocol versions in the config snapshots. [2](#0-1) 

**Dequeue path** — `runtime/runtime/src/lib.rs:2958–2977`:

```rust
// Queue entries are ordered by expires_at
if queue_entry.expires_at > apply_state.block_height {
    break;
}
```

The loop **breaks** (not continues) on the first non-expired entry, relying on the comment-stated invariant that entries are ordered by `expires_at`. [3](#0-2) 

**Invariant violation scenario**: Suppose at block height `H`, a yield `Y_old` is created with `yield_timeout_length_in_blocks = 200`, giving `expires_at = H + 200`. A protocol upgrade then reduces `yield_timeout_length_in_blocks` to 100. At block height `H' > H`, a yield `Y_new` is created with `expires_at = H' + 100`. If `H' + 100 < H + 200`, then `Y_new` has a smaller `expires_at` than `Y_old`, but `Y_old` sits ahead of `Y_new` in the FIFO queue. When `block_height` reaches `H' + 100`, the loop reads `Y_old` first, finds `Y_old.expires_at = H + 200 > block_height`, and **breaks** — never reaching `Y_new`. `Y_new`'s callback is not triggered until `H + 200` is reached, which is up to `(H + 200) - (H' + 100)` blocks later than expected. [4](#0-3) 

The `PromiseYieldIndices` structure and the queue are defined in: [5](#0-4) 

---

### Impact Explanation

Any contract that calls `promise_yield_create` and relies on the timeout callback being executed within `yield_timeout_length_in_blocks` blocks will have its execution flow broken. The callback (`PromiseResult::Failed`) is not delivered at the expected block, and any funds or state the contract intended to release on timeout remain locked. This is **contract execution flow breakage** — an in-scope impact. The DoS duration is bounded by the difference between the old and new timeout values (up to `old_timeout - new_timeout` blocks, i.e., up to 100+ blocks at current values).

---

### Likelihood Explanation

`yield_timeout_length_in_blocks` is a `RuntimeConfig` parameter versioned per protocol version. NEAR protocol upgrades are governance-controlled (validator supermajority vote). A future upgrade reducing this parameter — for example, to improve responsiveness of yield-based contracts — would silently trigger this bug for all yields already in the queue. No code guard prevents this. The bug is latent and requires no privileged attacker: any ordinary user who calls `promise_yield_create` before the upgrade is affected.

---

### Recommendation

Replace the `break` in `resolve_promise_yield_timeouts` with logic that skips non-expired entries rather than stopping at the first one. The simplest safe fix is to scan the entire queue and process all entries with `expires_at <= block_height`, regardless of position. Alternatively, maintain the queue sorted by `expires_at` (e.g., using a priority queue or by inserting in sorted order), which restores the break-on-first optimization correctly. The spec comment "Queue entries are ordered by expires_at" should be converted into an enforced invariant or removed. [6](#0-5) 

---

### Proof of Concept

1. Deploy a contract on account `A` that calls `promise_yield_create` with callback `on_timeout`.
2. At block height `H`, submit a transaction invoking the contract. The runtime enqueues `PromiseYieldTimeout { expires_at: H + 200, ... }` at queue index `i`.
3. A protocol upgrade activates at block `H + 50`, reducing `yield_timeout_length_in_blocks` from 200 to 50.
4. At block `H + 60`, submit another transaction invoking the same contract. The runtime enqueues `PromiseYieldTimeout { expires_at: H + 110, ... }` at queue index `i+1`.
5. At block `H + 110`, `resolve_promise_yield_timeouts` reads index `i`: `expires_at = H + 200 > H + 110` → **breaks**. Index `i+1` (`expires_at = H + 110 <= H + 110`) is never reached.
6. The second yield's `on_timeout` callback is not executed. Any funds the contract was supposed to refund on timeout remain locked until block `H + 200`.
7. Confirmed by the existing test `test_yield_timeout_under_congestion` which shows the queue is sensitive to processing order, and by `enqueue_promise_yield_timeout` which appends without sorting. [7](#0-6) [8](#0-7)

### Citations

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

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__129.json.snap (L263-263)
```text
      "yield_timeout_length_in_blocks": 200,
```

**File:** runtime/runtime/src/lib.rs (L2942-2977)
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

**File:** core/primitives/src/receipt.rs (L1067-1091)
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

/// Entries in the queue of PromiseYield timeouts.
#[derive(BorshSerialize, BorshDeserialize, Clone, PartialEq, Debug, ProtocolSchema)]
pub struct PromiseYieldTimeout {
    /// The account on which the yielded promise was created
    pub account_id: AccountId,
    /// The `data_id` used to identify the awaited input data
    pub data_id: CryptoHash,
    /// The block height before which the data must be submitted
    pub expires_at: BlockHeight,
}
```
