Based on my investigation, the strongest analog to the Xen CVE's "wrong assertion where the case needs handling → NULL pointer dereference on a release build" pattern is the `.unwrap()` call on a fallible cross-shard remapping result inside `receipt_filter_fn`, reachable from an unprivileged contract-deploy call.

### Title
Unhandled `receiver_shard_id` error in delayed-receipt filtering causes a transaction-triggered chunk-apply panic - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()` [1](#0-0)  on every delayed receipt popped during `pop()`, which is invoked unconditionally while draining the delayed-receipt queue during normal chunk application [2](#0-1) . `receiver_shard_id` is fallible for `GlobalContractDistribution` receipts: it returns `Err(EpochError::ShardingError(..))` when the receipt's stored `target_shard` can no longer be resolved via `resolve_to_current_shard` in the current shard layout [3](#0-2) . `resolve_to_current_shard` walks the shard split-history map (`ShardsSplitMapV3`/ancestor map) recorded per layout [4](#0-3) ; a `GlobalContractDistribution` receipt can sit in the delayed queue across multiple resharding events, and if its `target_shard` predates the tracked split history (or the mapping otherwise can't be resolved), `receiver_shard_id` legitimately returns `Err`, which `receipt_filter_fn`'s `.unwrap()` turns into a panic instead of an error the caller can propagate.

### Finding Description
Any account can trigger `DeployGlobalContract`, which creates a `GlobalContractDistribution` receipt that gets forwarded/buffered and can be delayed by the congestion-control gas/proof limits [5](#0-4) . If gas/proof limits keep the receipt in the delayed queue while the network reshards (a normal, protocol-driven event, not requiring any privileged action), the receipt is popped by `DelayedReceiptQueueWrapper::pop`, which calls `receipt_filter_fn` for every receipt to decide whether it belongs to the currently-applying shard [2](#0-1) . `receipt_filter_fn` unwraps the `Result` from `receiver_shard_id`, so any case where the target shard cannot be resolved crashes the node's `apply` call. Since `apply` runs identically on every validator/chunk-producer processing that chunk, this is a deterministic, chunk-processing halt rather than a single-node crash — i.e., a transaction (or receipt)-triggered halt of chunk production for the affected shard.

This matches the CVE's bug class precisely: an "assertion" (here, `.unwrap()`, functionally the same in Rust — panics on the error case) exists on a path where "the case actually needs handling," and the failure occurs on the release build.

There is already a regression test (`test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs`) explicitly built to probe this exact panic path via two sequential dynamic reshardings [6](#0-5) , with the test comment stating: "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations" [7](#0-6) . This test currently asserts the chain does *not* stall, meaning `resolve_to_current_shard`'s split-history tracking is presumed to cover this scenario for `DynamicResharding` (V3 layouts) — the test explicitly gates on `ProtocolFeature::DynamicResharding` and V3 layouts, and is skipped otherwise. I was not able to fully verify, within the given tool budget, whether `resolve_to_current_shard`'s ancestor-map coverage is complete for *all* resharding paths (e.g., static/V2 resharding, or resharding sequences longer than two hops, or edge cases in delayed-receipt persistence across epoch boundaries not exercised by this specific test) — this is the key remaining uncertainty.

### Impact Explanation
If a code path exists where `receiver_shard_id` returns `Err` for a delayed `GlobalContractDistribution` receipt not covered by the existing regression test's exact two-resharding scenario, the `.unwrap()` panic would deterministically crash `apply` on every node that processes that shard's chunk, producing a transaction-triggered halt of chunk production — matching the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Reaching the vulnerable code requires only an unprivileged `DeployGlobalContract` action plus normal protocol resharding, both of which are reachable by any signer without special permissions. However, the *precise* conditions under which `resolve_to_current_shard` fails to resolve the stale target shard are narrow and appear to already be guarded against for the tested double-resharding scenario, so likelihood is contingent on finding an untested resharding sequence or layout-history gap, which I could not confirm.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation (return a `Result` from `pop`/`receipt_filter_fn` instead of panicking), and add coverage for additional resharding sequences (e.g., three or more sequential splits, mixed static/dynamic resharding) to ensure `resolve_to_current_shard`'s ancestor map cannot be exhausted by a sufficiently delayed `GlobalContractDistribution` receipt.

### Proof of Concept
Conceptual reproduction path (not independently executed beyond reading the existing test):
1. Deploy a global contract from an account whose shard will be force-split by dynamic resharding, causing a `GlobalContractDistribution` receipt to be created.
2. Saturate the shard's gas/proof limits so the receipt is pushed to the delayed-receipt queue rather than processed immediately.
3. Trigger enough sequential resharding events (beyond what `resolve_to_current_shard`'s tracked split history can resolve) while the receipt remains delayed.
4. When the delayed queue drains and `pop()`/`receipt_filter_fn` runs against the stale `target_shard`, `receiver_shard_id().unwrap()` panics, halting chunk application deterministically on all nodes processing that shard.

**Uncertainty flag:** I could not confirm within budget whether such an unresolvable sequence is actually reachable given the existing fix/test in `test_stale_global_contract_distribution_after_double_resharding`, or whether `resolve_to_current_shard`'s ancestor-map fully closes this gap for all resharding paths (V2 static vs V3 dynamic, arbitrary hop counts). This should be verified with a Devin session that can run the test suite and extend the resharding depth/variants to confirm whether the panic is truly unreachable or only partially mitigated.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-908)
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
```

**File:** core/primitives/src/receipt.rs (L437-466)
```rust
    pub fn receiver_shard_id(&self, shard_layout: &ShardLayout) -> Result<ShardId, EpochError> {
        let shard_id = match self.receipt() {
            ReceiptEnum::Action(_)
            | ReceiptEnum::ActionV2(_)
            | ReceiptEnum::Data(_)
            | ReceiptEnum::PromiseYield(_)
            | ReceiptEnum::PromiseYieldV2(_)
            | ReceiptEnum::PromiseResume(_) => {
                shard_layout.account_id_to_shard_id(self.receiver_id())
            }
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
        };
        Ok(shard_id)
    }
```

**File:** core/primitives/src/shard_layout/v3.rs (L10-26)
```rust
/// A mapping from the parent shard to child shards. It maps shards from the
/// previous shard layout to shards that they split to in this shard layout.
/// Unlike previous versions of `ShardsSplitMap`, this one:
///   * Only includes shards that are actually split.
///   * Includes the full history of shard splits, i.e. split map of the current
///     layout is a superset of the split map of its parent layout.
///
/// For example if a shard layout with shards `[0, 2, 3, 4]` and split map `{1 => [3, 4]}`
/// splits shard 2 into shards [5, 6] the ShardSplitMap in the resulting layout will be:
/// `{1 => [3, 4], 2 => [5, 6]}`.
pub type ShardsSplitMapV3 = BTreeMap<ShardId, Vec<ShardId>>;

/// A mapping from the child shard to all its ancestors. Parent shard is the first
/// element of the ancestors vector, 'grandparent' shard is the second element, etc.
/// IDs of shards which have no ancestors (i.e. were *not* created by a split) are
/// not present in the mapping.
type ShardsAncestorMapV3 = BTreeMap<ShardId, Vec<ShardId>>;
```

**File:** runtime/runtime/src/global_contracts.rs (L25-63)
```rust
pub(crate) fn action_deploy_global_contract(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    apply_state: &ApplyState,
    deploy_contract: &DeployGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_deploy_global_contract").entered();

    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;

    Ok(())
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
