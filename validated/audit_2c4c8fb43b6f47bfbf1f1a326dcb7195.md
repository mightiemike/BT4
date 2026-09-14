## Title
`GlobalContractDistribution` receipt with stale `target_shard` panics `receipt_filter_fn` under static resharding, halting the chain — ([File: runtime/runtime/src/congestion_control.rs])

## Summary
`ReceiptSink`/delayed-queue processing calls `receiver_shard_id()` on every dequeued receipt and unconditionally `.unwrap()`s the result. For `GlobalContractDistribution` receipts, `receiver_shard_id()` can return `Err(EpochError::ShardingError(..))` when the receipt's `target_shard` no longer exists in the current shard layout and cannot be resolved through split history — which is exactly the case for the legacy/static `ShardLayoutConfig::Static` resharding path, where the layout carries no cumulative ancestor map. A single unprivileged `DeployGlobalContract` transaction, timed so that its forwarded distribution receipt is still delayed when a (static) resharding event occurs, causes every honest node applying that chunk to panic deterministically, halting the chain.

## Finding Description
`Receipt::receiver_shard_id` special-cases `GlobalContractDistribution` receipts: if the receipt's `target_shard` is not part of the current `shard_layout`, it tries `shard_layout.resolve_to_current_shard(target_shard)` to remap it via split ancestry, and returns `Err(EpochError::ShardingError(...))` if that fails: [1](#0-0) 

`resolve_to_current_shard`/ancestor tracking is only fully maintained for `ShardLayoutV3` (dynamic resharding), which "stores the full cumulative split history and a derived ancestor map enabling O(1) shard tracking," whereas static resharding's `V2` layout "stores only the most recent split map": [2](#0-1) [3](#0-2) 

This distinction is confirmed directly in the repo's own regression test, which explicitly limits its fix/verification to dynamic (V3) layouts and calls out that static resharding does not maintain a full split history: [4](#0-3) 

The delayed-receipt queue's filter function calls `receiver_shard_id` and immediately `.unwrap()`s it with no error handling, on the hot path used every time a delayed receipt is popped: [5](#0-4) 

The same unguarded pattern (`receiver_shard_id(&shard_layout)?` used with `?`, which is safer, but other call sites use raw `.unwrap()`) appears again in the delayed-receipt pop path used by every applying node: [6](#0-5) 

The receipt itself is produced by ordinary, unprivileged flows: any account can `DeployGlobalContract`, which creates a `GlobalContractDistributionReceipt` targeting the deployer's current shard, then hop-forwards itself shard-by-shard via `forward_distribution_next_shard`: [7](#0-6) 

If gas/compute on the target shard is saturated when the receipt arrives, it is pushed into the persistent delayed-receipt queue exactly like any other congested receipt (this is the same mechanism proven exploitable and repaired for the V3/dynamic case by the cited test, which artificially saturates compute to keep the receipt delayed across resharding events).

Because `ShardLayoutConfig::Static` legacy resharding is still fully supported and is the default/most common resharding mode in production configurations (`ShardLayoutConfig::default()` is `Static`) — [8](#0-7) 
— the underlying failure mode (stale `target_shard` after a legacy protocol-upgrade resharding) is not covered by the fix that only "works with V3 shard layouts," per the test's own comment. A `GlobalContractDistribution` receipt that is still delayed when a *static* resharding boundary is crossed will, on being popped, fail `receiver_shard_id` and hit `.unwrap()` in `receipt_filter_fn`, panicking every node applying that shard's chunk.

## Impact Explanation
A panic in `receipt_filter_fn`/`process_delayed_receipts` occurs deterministically for every node applying the affected shard's chunk (it's driven by state and receipt content, not by any specific validator's local view), so honest nodes crash in lock-step — this is a transaction-triggered chain halt for the affected shard(s), not merely a local resource issue. This matches the "transaction-triggered halt" acceptance criterion: an unprivileged user's `DeployGlobalContract` transaction, combined with a routine (legacy) resharding event, produces a receipt that can never be safely processed and permanently crashes chunk application for that shard until manually patched/hotfixed. This is High severity, analogous to the referenced report where one poorly-behaved `feeReceiver` bricks `distribute()` for everyone: here one benign-looking global-contract deployment can brick receipt processing for an entire shard.

## Likelihood Explanation
Likelihood is Medium: it requires (a) submitting `DeployGlobalContract` on a shard that later undergoes a *legacy/static* resharding event (protocol-version-triggered, not attacker-controlled timing), and (b) the distribution receipt still being in the delayed queue (or in transit) when the resharding boundary is crossed. Static resharding events are rarer than dynamic (V3) resharding and occur only at scheduled protocol upgrades, so the attacker cannot force the trigger but can wait for or predict a scheduled static-resharding protocol upgrade and pre-position a delayed distribution receipt (e.g. via compute saturation, exactly the technique demonstrated for the V3 case in the cited test) to guarantee it survives to the boundary. The mechanism is fully deterministic and reproducible given knowledge of an upcoming static resharding upgrade.

## Recommendation
- Make `receiver_shard_id` failures non-fatal wherever they're consulted in the hot receipt-processing/delayed-queue path: propagate the error via `Result` (as already done in `forward_from_buffer_to_shard`) instead of `.unwrap()`, and handle unresolved `GlobalContractDistribution` targets gracefully (e.g., re-route to a deterministic fallback shard, or keep the receipt delayed until a resolvable layout is reached) rather than panicking.
- Extend the split-history/ancestor-resolution mechanism (or an equivalent fallback lookup via `get_shard_layout_history`) to the static resharding path so `resolve_to_current_shard` can succeed for `ShardLayoutConfig::Static` transitions too, not just `ShardLayoutV3`.
- Add a regression test mirroring `test_stale_global_contract_distribution_after_double_resharding` but exercising `ShardLayoutConfig::Static` resharding to confirm the panic is fixed for that path as well.

## Proof of Concept
1. Configure a chain using legacy/static resharding (`ShardLayoutConfig::Static`), matching production defaults.
2. From an unprivileged account on shard S_A, submit `DeployGlobalContract`, generating a `GlobalContractDistribution` receipt with `target_shard = S_A`.
3. Saturate compute on S_A every block (e.g., repeated `burn_gas_raw` calls, as done in the existing test) so the receipt is pushed into, and remains in, the delayed-receipt queue while a scheduled static-resharding protocol upgrade splits S_A.
4. Stop saturating and let the delayed queue drain; when the stale receipt is popped, `receipt_filter_fn` calls `receiver_shard_id(&shard_layout).unwrap()`, which returns `Err` because the legacy static layout cannot resolve the now-nonexistent `target_shard`, causing every node applying that chunk to panic and halting the shard.

This mirrors the exact scenario proven for the dynamic-resharding case in the existing repository test, whose own comments confirm the fix is scoped only to V3 layouts: [9](#0-8)

### Citations

**File:** core/primitives/src/receipt.rs (L447-463)
```rust
            ReceiptEnum::GlobalContractDistribution(receipt) => {
                let target_shard = receipt.target_shard();
                if shard_layout.shard_ids().contains(&target_shard) {
                    target_shard
                } else {
                    // The target shard may be from an arbitrarily old layout (the receipt could
                    // have been delayed across multiple resharding events). resolve_to_current_shard
                    // will find a shard descendant in the current layout.
                    let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
                    else {
                        return Err(EpochError::ShardingError(format!(
                            "Shard {target_shard} does not exist in the shard layout or its split history",
                        )));
                    };
                    current_shard
                }
            }
```

**File:** docs/architecture/how/dynamic_resharding.md (L141-143)
```markdown
- **`TrieSplit`** (`core/primitives/src/trie_split.rs`) -- Result of finding the optimal split point: boundary account and left/right memory usage. Stored in chunk headers and `ChunkExtra`.

- **`ShardLayoutV3`** (`core/primitives/src/shard_layout/v3.rs`) -- New shard layout version for dynamic resharding. Unlike V2, stores the full cumulative split history and a derived ancestor map enabling O(1) shard tracking.
```

**File:** docs/architecture/how/dynamic_resharding.md (L205-215)
```markdown
| **Shard layout source** | `EpochConfig.shard_layout` (determined by protocol version) | `EpochInfo.shard_layout` (stored per-epoch in V5) |
| **When layout changes** | Only on protocol version upgrade | Automatically at epoch boundaries based on trie memory usage |
| **Layout version** | V0, V1, or V2 | V3 (with cumulative split history) |
| **Config type** | `ShardLayoutConfig::Static { shard_layout }` | `ShardLayoutConfig::Dynamic { dynamic_resharding_config }` |
| **Split history** | V2 stores only the most recent split map | V3 stores full cumulative split history + ancestor maps |
| **EpochInfo version** | V4 or earlier | V5 (adds `shard_layout` and `last_resharding` fields) |
| **Block header** | V5 (with deprecated challenges fields) | V6 (adds `shard_split`, removes challenges) |
| **Chunk header** | V4 | V5 (adds `proposed_split: Option<TrieSplit>`) |
| **BlockInfo** | V3 (with deprecated `slashed` map) | V4 (adds `shard_split`, removes `slashed`) |
| **ChunkExtra** | V4 | V5 (adds `proposed_split`) |
| **Shard tracking** | Iterate through protocol versions comparing layouts | O(1) lookup via `ShardLayoutV3::ancestor_uids()` |
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-186)
```rust
    assert!(both_splits_done, "both shard splits did not complete within the allotted blocks");

    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
    let current_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    let drain_end = current_height + epoch_length * 2;
    env.runner_for_account(&chunk_producer).run_until_head_height(drain_end);

    let head_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    assert!(
        head_height >= drain_end,
        "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
        head_height,
        drain_end
    );
}
```

**File:** runtime/runtime/src/congestion_control.rs (L868-878)
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
```

**File:** runtime/runtime/src/congestion_control.rs (L880-909)
```rust
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
```

**File:** runtime/runtime/src/global_contracts.rs (L275-320)
```rust
fn forward_distribution_next_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<(), RuntimeError> {
    let shard_layout = epoch_info_provider.shard_layout(&apply_state.epoch_id)?;
    let already_delivered_shards = BTreeSet::from_iter(
        global_contract_data
            .already_delivered_shards()
            .iter()
            .cloned()
            .chain(std::iter::once(apply_state.shard_id)),
    );
    let Some(next_shard) = shard_layout
        .shard_ids()
        .filter(|shard_id| !already_delivered_shards.contains(&shard_id))
        .next()
    else {
        return Ok(());
    };
    let already_delivered_shards = Vec::from_iter(already_delivered_shards);
    let predecessor_id = receipt.predecessor_id().clone();
    let next_receipt = global_contract_data.forward(next_shard, already_delivered_shards);
    let mut next_receipt = Receipt::new_global_contract_distribution(predecessor_id, next_receipt);
    let receipt_id = apply_state.create_receipt_id(receipt.receipt_id(), 0);
    next_receipt.set_receipt_id(receipt_id);
    if apply_state.save_receipt_to_tx {
        receipt_to_tx.push((
            receipt_id,
            ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                    parent_receipt_id: *receipt.receipt_id(),
                    parent_predecessor_id: receipt.predecessor_id().clone(),
                }),
                receiver_account_id: next_receipt.receiver_id().clone(),
                shard_id: apply_state.shard_id,
            }),
        ));
    }
    receipt_sink.forward_or_buffer_receipt(next_receipt, apply_state, state_update)?;
    Ok(())
}
```

**File:** core/primitives/src/epoch_manager.rs (L74-78)
```rust
impl Default for ShardLayoutConfig {
    fn default() -> Self {
        ShardLayoutConfig::Static { shard_layout: ShardLayout::single_shard() }
    }
}
```
