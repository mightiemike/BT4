I found the key analog. `yield_timeout_length_in_blocks` is a runtime config parameter (`core/parameters/src/vm.rs:193`, values in `parameters.yaml`/`parameters_testnet.yaml`, e.g. `200` blocks) that determines `expires_at = block_height + yield_timeout_length_in_blocks` when a `PromiseYieldTimeout` entry is enqueued (`enqueue_promise_yield_timeout`, `core/store/src/utils/mod.rs:181-198`). This value can change across protocol versions via the `RuntimeConfigStore` (parameter changes are how nearcore evolves runtime parameters at protocol upgrades). The `PromiseYieldTimeout` queue is a strict FIFO (`TrieQueue`, `core/store/src/trie/receipts_column_helper.rs:63-122`) and `resolve_promise_yield_timeouts` explicitly assumes monotonic ordering by comment "Queue entries are ordered by expires_at" and unconditionally `break`s the entire loop on the first entry whose `expires_at > apply_state.block_height` [1](#0-0) , exactly matching the reported bug pattern in the Cosmos `andromeda-validator-staking` contract.

### Title
Reduced `yield_timeout_length_in_blocks` breaks FIFO-ordering assumption in `resolve_promise_yield_timeouts`, stalling timeout processing for later yields - (File: `runtime/runtime/src/lib.rs`)

### Summary
`resolve_promise_yield_timeouts` walks the persistent `PromiseYieldTimeout` queue strictly FIFO and stops the entire loop the moment it hits an entry whose `expires_at > apply_state.block_height`, under the explicit assumption "Queue entries are ordered by expires_at" [2](#0-1) . `expires_at` is computed at yield-creation time as `block_height + yield_timeout_length_in_blocks`, where `yield_timeout_length_in_blocks` is a `RuntimeConfig`/`vm::Config` parameter (`core/parameters/src/vm.rs:193`) that can be changed between protocol versions via the `RuntimeConfigStore` [3](#0-2) .

### Finding Description
A `promise_yield_create` (or `promise_yield_create_with_id`) call enqueues a `PromiseYieldTimeout { account_id, data_id, expires_at }` at the back of a FIFO trie-backed queue via `enqueue_promise_yield_timeout` [4](#0-3) , using `TrieQueue::push_back` (`core/store/src/trie/receipts_column_helper.rs:83-98`). The queue is drained strictly from `first_index` upward. If protocol version P has `yield_timeout_length_in_blocks = X` and a later protocol version P' (activated via a validator-voted protocol upgrade) sets it to a smaller value `X' < X`, then a yield created under P (queued first, `expires_at = h + X`) can sit in front of a yield created later under P' (`expires_at = h' + X'` where `h' + X' < h + X`) whose timeout has already legitimately arrived. Because `resolve_promise_yield_timeouts` unconditionally `break`s on the first non-expired entry rather than skipping it [1](#0-0) , the later, already-expired entry is never reached until the earlier entry ahead of it in the queue also expires — this is architecturally identical to the reported Cosmos bug where reducing `UnbondingTime` broke the FIFO ordering assumption of the unstaking queue.

### Impact Explanation
The practical consequence is a delayed/`Failed` resolution of an in-flight `yield_create` promise for a contract that would otherwise be entitled to timeout at the correct height. This is a liveness/availability defect for cross-contract callback flows relying on yield timeouts (funds or callback logic gated on the yield can be stuck until the earlier, longer-timeout entry finally expires). Because `yield_timeout_length_in_blocks` changes only occur at a network-wide protocol upgrade (not an arbitrary transaction-triggered event) and all validators apply the same deterministic queue/parameter transition, this does not cause state-root divergence between honest nodes — it is a self-consistent but incorrect chunk-application ordering, i.e. a bounded DoS/liveness issue rather than a consensus split, double-spend, or fund-freeze at the protocol-invariant level (the timeout resume is still eventually generated once earlier entries drain).

### Likelihood Explanation
Triggering requires (a) an in-flight yield created shortly before a protocol upgrade that lowers `yield_timeout_length_in_blocks`, and (b) another yield created after the upgrade with a shorter timeout window landing behind it in the same shard's queue. Given `yield_timeout_length_in_blocks` has been static at `200` blocks across all observed protocol-version snapshots in this codebase [5](#0-4) , there is no evidence this parameter has ever actually been reduced across a live protocol upgrade; it would require a deliberate future governance decision to lower it, which is a narrower trigger surface than the Cosmos case (where `UnbondingTime` is chain-governance-controlled and can change independently of the audited contract's deploy schedule).

### Recommendation
Change `resolve_promise_yield_timeouts` to not break the loop unconditionally on the first non-expired entry when parameter changes are possible; either (a) keep the FIFO break but document/enforce an invariant that `yield_timeout_length_in_blocks` must never decrease across protocol versions, or (b) change the loop to skip (not break) past entries whose `expires_at > block_height` up to a bounded lookahead, similar to the Sherlock recommendation, while preserving compute/gas metering guarantees.

### Proof of Concept
Not independently reproduced against a live network; the code path and parameter mutability described above are read directly from `runtime/runtime/src/lib.rs:3113-3220`, `core/store/src/utils/mod.rs:181-198`, and `core/parameters/src/vm.rs:193`, showing the same class of ordering-assumption break as the referenced Sherlock finding, contingent on a future reduction of `yield_timeout_length_in_blocks`.

### Citations

**File:** runtime/runtime/src/lib.rs (L3129-3148)
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
```

**File:** core/parameters/res/runtime_configs/parameters.yaml (L320-321)
```yaml
yield_timeout_length_in_blocks: 200
max_yield_payload_size: 1_024 # kiB
```

**File:** core/store/src/utils/mod.rs (L181-198)
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

**File:** core/parameters/res/runtime_configs/parameters.snap (L256-256)
```text
yield_timeout_length_in_blocks                           200
```
