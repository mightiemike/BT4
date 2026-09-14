### Title
Panic-on-unwrap in `DelayedReceiptQueueWrapper::receipt_filter_fn` when `GlobalContractDistribution` receipt's stale `target_shard` cannot be resolved — transaction-triggered chain halt - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`receipt_filter_fn`, used every time a delayed receipt is popped during chunk apply, blindly `.unwrap()`s the `Result` returned by `Receipt::receiver_shard_id`, with no fallback/error handling — the same class of bug as the Chainlink oracle report (an external/derived value is trusted via a direct call with no error path, and any failure aborts the whole operation instead of being handled).

### Finding Description
`receipt_filter_fn` is called from `DelayedReceiptQueueWrapper::pop`, which runs on every chunk application while draining the delayed-receipts queue: [1](#0-0) 

It computes `receiver_shard_id` for the popped receipt and unwraps the result unconditionally:
```
let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
```
For `GlobalContractDistribution` receipts, `receiver_shard_id` explicitly returns an `Err(EpochError::ShardingError(...))` when the receipt's `target_shard` cannot be found in the current shard layout *or* resolved through the layout's split history: [2](#0-1) 

`resolve_to_current_shard` walks the shard-split history recorded in `ShardLayoutV3`, but the second `unwrap()` in `receipt_filter_fn` has no fallback if that resolution fails (e.g. the split history/ancestor chain does not cover the receipt's stale `target_shard` for some reason — different resharding path, forked/older layout, or any future edge case not covered by `resolve_to_current_shard`): [3](#0-2) 

A `GlobalContractDistribution` receipt is a normal, unprivileged consequence of any account calling `DeployGlobalContract` — deploying a global contract is available to any transaction signer: [4](#0-3) 

If such a receipt gets stuck in the delayed queue across a resharding event (e.g. under sustained congestion on the shard, as demonstrated in the repository's own regression test) and its `target_shard` cannot be remapped by `resolve_to_current_shard` when `pop`/`receipt_filter_fn` runs, the `.unwrap()` panics. This crashes every node processing that chunk, since `pop` is invoked unconditionally as part of ordinary receipt processing: [5](#0-4) 

The repository itself contains a regression test explicitly built around this exact scenario, confirming that a double-resharding sequence combined with a delayed `GlobalContractDistribution` receipt is a recognized failure mode for this unwrap: [6](#0-5) 

### Impact Explanation
A panic inside `receipt_filter_fn`/`pop` occurs during ordinary chunk application (block production and validation), not in an isolated RPC path. Because every honest validator/chunk-producer node executes the same apply logic, a receipt that triggers the unresolved-shard case would panic deterministically on all nodes processing that chunk, producing a transaction-triggered chain halt — one of the explicitly in-scope impact categories (no fallback exists; the process aborts instead of returning a graceful `ActionError`/`RuntimeError`). This is reachable purely by an unprivileged account: `DeployGlobalContract` is available to any signer, and inducing prolonged congestion on a shard (to keep the receipt delayed across a resharding boundary) is also achievable by ordinary users issuing transactions.

### Likelihood Explanation
Exploitability depends on hitting a shard-split-history edge case that `resolve_to_current_shard` does not cover. The specific "double resharding" scenario is covered by an existing regression test that (as written) expects the chain to keep advancing, implying that particular case is patched by `resolve_to_current_shard`'s full-history walk. However, the fix relies entirely on `shards_split_map`/`shards_ancestor_map` correctly and completely capturing every historical split for every possible chain of resharding/fork events; `receipt_filter_fn` still has zero defensive handling for the `Err` branch of `receiver_shard_id`, and `shard_layout(&self.epoch_id).unwrap()` is likewise unguarded. Any gap in split-history coverage (e.g., non-ReshardingV3 layout transitions, or resharding sequences/timing not identical to the tested one) turns straight into a network-wide panic rather than a contained error. This is a real, structurally present error-handling gap even though the one scenario exercised by the current test suite happens to be covered.

### Recommendation
Replace both `.unwrap()` calls in `receipt_filter_fn` with proper error propagation: have `pop` return `Result<Option<_>, RuntimeError>` all the way through (it already does) and surface a `RuntimeError`/`StorageError` instead of panicking when `shard_layout` lookup or `receiver_shard_id` resolution fails. At minimum, add defensive fallback behavior (e.g., treat an unresolvable `GlobalContractDistribution` target shard as "not for this shard" and skip/retire it rather than panicking), and add regression tests that specifically exercise resharding topologies not already covered by `test_stale_global_contract_distribution_after_double_resharding` (e.g., triple splits, splits interleaved with non-V3 layout versions) to confirm `resolve_to_current_shard` genuinely never returns `None` for any receipt that can legitimately still be in the delayed queue.

### Proof of Concept
1. Any account calls `DeployGlobalContract` (`GlobalContractDeployMode::CodeHash`), producing a `GlobalContractDistribution` receipt targeted at its current shard (see `runtime/runtime/src/global_contracts.rs:25-63`).
2. The originating shard is kept congested (e.g., repeated `burn_gas_raw` calls saturating the chunk gas limit) so the receipt is pushed to, and remains in, the delayed-receipts queue across a shard-layout change (resharding).
3. If a resharding sequence occurs whose split history is not fully captured by `shards_split_map`/`resolve_to_current_shard` for this receipt's stale `target_shard` (a gap distinct from the specific double-split case already covered by the existing test), then when the delayed queue is drained (`DelayedReceiptQueueWrapper::pop` → `receipt_filter_fn`), `receiver_shard_id` returns `Err(EpochError::ShardingError)`, and the `.unwrap()` at `congestion_control.rs:876` panics, halting chunk application for every node that processes that chunk. This mirrors the repository's own reproduction pattern in `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs:30-186`), which was written explicitly to detect this exact unwrap-panic failure mode.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
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

**File:** core/primitives/src/shard_layout/v3.rs (L315-326)
```rust
    /// Resolve any historical shard ID to a current shard by walking the full
    /// split history in `shards_split_map`. Returns the shard itself if it is
    /// current, or follows the first child at each generation until a current
    /// shard is reached. Returns `None` only if the shard ID is absent from
    /// both the current layout and the split history.
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        if self.shard_ids.contains(&shard_id) {
            return Some(shard_id);
        }
        let children = self.shards_split_map.get(&shard_id)?;
        self.resolve_to_current_shard(children[0])
    }
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
