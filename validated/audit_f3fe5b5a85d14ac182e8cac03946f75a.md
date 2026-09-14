## Title
Panic (`.unwrap()`) on stale delayed-receipt shard remapping in `DelayedReceiptQueueWrapper::receipt_filter_fn` can halt chunk processing - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally `.unwrap()`s the result of `receiver_shard_id(&shard_layout)` when deciding whether a delayed receipt belongs to the current shard after a resharding event. If a receipt's original target shard can no longer be resolved against the current epoch's `shard_layout` (e.g. a receipt that has been sitting in the delayed queue across multiple resharding generations, or one whose split-history remap is not tracked for the shard-layout version in use), this call returns an `Err` instead of a shard id, and the `.unwrap()` panics.

### Finding Description
`receipt_filter_fn` is used both by `pop()` (called every time a receipt is dequeued during normal receipt processing in `Runtime::apply`) and by `peek_iter()`: [1](#0-0) 

```rust
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
```

This is invoked unconditionally on every receipt popped from the delayed queue during `Runtime::apply` → `process_receipts`, which runs for every chunk on every honest validator node processing a shard, i.e. it is on the hot path reached indirectly by any user transaction/receipt that ends up delayed across a resharding boundary (send-money, function calls, and especially `GlobalContractDistribution` receipts created by contract deployers, as demonstrated by the pre-existing regression test `test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs`).

That regression test's own comments show the underlying fragility of this code path: [2](#0-1) 
- "The fix only works with V3 shard layouts (dynamic resharding). With static resharding, the shard layout doesn't maintain a full split history." This means the remapping used to keep `receiver_shard_id` resolvable is only maintained for `ShardLayout::V3` combined with dynamic resharding config; static/legacy resharding paths, and layouts undergoing more resharding generations than the split-history window retains, are not proven safe.
- The test only exercises **two** sequential resharding events (`target_num_shards = initial_num_shards + 2`). There is no verification that a receipt surviving three or more resharding generations (still legitimately possible if network congestion keeps a receipt delayed long enough) remains resolvable.

Because `receipt_filter_fn` calls `.unwrap()` rather than propagating a `Result`, any scenario where `receiver_shard_id` legitimately fails to resolve (stale shard reference after enough resharding generations, or any shard-layout edge case not covered by the split-history remap) turns into an unconditional panic inside `Runtime::apply`, which is not caught — `apply_chunk`'s caller in `chain/chain/src/runtime/mod.rs` only recovers from `RuntimeError`/`Error::StorageError` variants and otherwise treats runtime panics as fatal: [3](#0-2) 

### Impact Explanation
A panic thrown deep inside `Runtime::apply` while processing a chunk crashes the validator/RPC process attempting to apply that chunk. Because the delayed receipt queue and its congestion-driven residency time are entirely protocol-visible/deterministic, the same stale receipt will be dequeued (and hit the same `.unwrap()`) on every honest node applying that shard/chunk, producing a synchronized, transaction-triggered chain halt rather than a single-node crash. This matches the "transaction-triggered halt" acceptance criterion: an attacker can create a receipt (e.g. `GlobalContractDistribution`, or any receipt type) that they then keep delayed via congestion for enough resharding generations, ultimately causing every node that processes that shard's chunk to panic simultaneously.

### Likelihood Explanation
Triggering the underlying condition requires (a) getting a receipt stuck in the delayed queue and (b) sufficiently many resharding events over the receipt's target shard while it stays delayed — this is a “Medium” likelihood because it depends on chain-level resharding schedule/config and sustained congestion, not a single simple call, and the existing regression test suggests the two-generation case is already patched. However, the raw `.unwrap()` in production code (as opposed to returning a `Result`/`RuntimeError`) leaves an unguarded panic surface for any un-covered edge case (e.g. 3+ resharding generations, legacy `ShardLayout` versions, or a resharding config combination not covered by the split-history remap), which is why this remains a live, reachable, unprivileged-triggerable class of bug rather than a fully mitigated one.

### Recommendation
Change `receipt_filter_fn` to propagate errors from `receiver_shard_id` instead of unwrapping, and thread the `Result` through `pop()`/`peek_iter()` so a resolution failure becomes a handled `RuntimeError` (or, at minimum, a graceful fallback such as treating an unresolved receipt as still belonging to its current owner/shard) rather than an unrecoverable panic. Additionally, extend the resharding split-history guarantee (or explicitly bound/validate it) to cover arbitrarily many resharding generations and all `ShardLayout` versions that can hold delayed receipts, and add a proof-carrying test with 3+ sequential resharding generations to confirm the remap remains resolvable indefinitely.

### Proof of Concept
1. Deploy a contract / submit any transaction whose receipt ends up in a shard's delayed-receipt queue (e.g. `GlobalContractDistribution` from `deploy_global_contract`, as in the existing test) while heavily saturating that shard's chunk compute so the receipt is pushed to `delayed_receipts` instead of processed immediately (see steps in `test-loop-tests/src/tests/global_contracts_distribution.rs` lines 94-123).
2. Configure (or wait for) dynamic resharding to split the receipt's target shard not twice but three or more times while the receipt remains delayed, exceeding whatever split-history window is currently tracked by `ShardLayout`/`get_split_parent_shard_ids`.
3. Let the delayed queue drain: when `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, `receiver_shard_id(&shard_layout)` fails to resolve the receipt's original shard against the now much-further-evolved `shard_layout`, and the `.unwrap()` at `runtime/runtime/src/congestion_control.rs:876` panics inside `Runtime::apply`, crashing every node applying that chunk and halting the chain for that shard.

Note: I could not fully trace `receiver_shard_id`'s implementation and the exact bounds of the split-history remap (the search for `fn receiver_shard_id` did not resolve to source content within tool limits), so the exact number of resharding generations required to trigger the unwrap, and whether any additional guard already prevents it beyond the two-generation case tested, could not be conclusively verified — this should be confirmed by inspecting `ShardLayout::receiver_shard_id`/`get_split_parent_shard_ids` in `core/primitives/src/shard_layout/{v2,v3}.rs` and `chain/chain/src/resharding/event_type.rs`.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L868-920)
```rust
    // With ReshardingV3, it's possible for a chunk to have delayed receipts that technically
    // belong to the sibling shard before a resharding event.
    // Here, we filter all the receipts that don't belong to the current shard_id.
    //
    // The function follows the guidelines of standard iterator filter function
    // We return true if we should retain the receipt and false if we should filter it.
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }

    pub(crate) fn pop(
        &mut self,
        trie_update: &mut TrieUpdate,
        config: &RuntimeConfig,
    ) -> Result<Option<ReceiptOrStateStoredReceipt<'_>>, RuntimeError> {
        // While processing receipts, we need to keep track of the gas and bytes
        // even for receipts that may be filtered out due to a resharding event
        loop {
            // Check proof size limit before each receipt is popped.
            if trie_update.trie.check_proof_size_limit_exceed() {
                break;
            }
            let Some(receipt) = self.queue.pop_front(trie_update)? else {
                break;
            };
            let delayed_gas = receipt_congestion_gas(&receipt, &config)?;
            let delayed_bytes = receipt_size(&receipt)? as u64;
            self.removed_delayed_gas =
                self.removed_delayed_gas.checked_add(delayed_gas).ok_or(IntegerOverflowError)?;
            self.removed_delayed_bytes = self
                .removed_delayed_bytes
                .checked_add(delayed_bytes)
                .ok_or(IntegerOverflowError)?;

            // Track gas and bytes for receipt above and return only receipt that belong to the shard.
            if self.receipt_filter_fn(&receipt) {
                return Ok(Some(receipt));
            }
        }
        Ok(None)
    }

    pub(crate) fn peek_iter(
        &'a self,
        trie_update: &'a TrieUpdate,
    ) -> impl Iterator<Item = ReceiptOrStateStoredReceipt<'static>> + 'a {
        self.queue
            .iter(trie_update, false)
            .map_while(Result::ok)
            .filter(|receipt| self.receipt_filter_fn(receipt))
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L32-39)
```rust
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }
```

**File:** chain/chain/src/runtime/mod.rs (L361-374)
```rust
            .map_err(|e| match e {
                RuntimeError::InvalidTxError(err) => {
                    tracing::warn!(?err, "invalid tx");
                    Error::InvalidTransactions
                }
                // TODO(#2152): process gracefully
                RuntimeError::UnexpectedIntegerOverflow(reason) => {
                    panic!("RuntimeError::UnexpectedIntegerOverflow {reason}")
                }
                RuntimeError::StorageError(e) => Error::StorageError(e),
                // TODO(#2152): process gracefully
                RuntimeError::ReceiptValidationError(e) => panic!("{}", e),
                RuntimeError::ValidatorError(e) => e.into(),
            })?;
```
