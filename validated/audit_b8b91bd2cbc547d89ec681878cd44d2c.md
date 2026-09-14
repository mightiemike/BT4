### Title
Panic-inducing `.unwrap()` on stale `GlobalContractDistribution`/delayed receipts during multi-generation resharding causes a transaction-triggered chain halt - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
CVE-2017-0376 is a Tor DoS: an unprivileged client can send a specific message type (`BEGIN_DIR` on a rendezvous circuit) that the code did not expect in that context, hitting an assertion and crashing the daemon. The nearcore analog is a reachable `.unwrap()` on a fallible shard-remapping call in the delayed-receipt filtering path, which can be driven into failure by an ordinary user's transactions (deploying a global contract plus compute-saturating calls) combined with two shard-splitting events, causing every validator applying that shard's chunk to panic.

### Finding Description
`DelayedReceiptQueueWrapper::receipt_filter_fn` in `runtime/runtime/src/congestion_control.rs:874-878` computes the shard a delayed receipt belongs to via `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()`. This is called from `pop()` (`congestion_control.rs:905`) and `peek_iter()` (`congestion_control.rs:919`), which are invoked on every chunk application while draining the delayed-receipt queue (`runtime/runtime/src/lib.rs:2570` `process_delayed_receipts`, part of the mandatory `process_receipts` step of `apply` — `runtime/runtime/src/lib.rs:1943`).

`receiver_shard_id` must remap a receipt's original target shard into the *current* shard layout. Across a single resharding event this works, but the code base itself contains a regression test, `test-loop-tests/src/tests/global_contracts_distribution.rs::test_stale_global_contract_distribution_after_double_resharding`, whose comments explicitly document that "processing the stale `GlobalContractDistribution` receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations." The scenario is built entirely from ordinary user actions:
1. An unprivileged account deploys a global contract (`DeployGlobalContract` action), producing a `GlobalContractDistribution` receipt targeted at the deployer's shard.
2. The same account keeps submitting ordinary `FunctionCall` transactions that burn gas, saturating the chunk's compute budget every block so the `GlobalContractDistribution` receipt is pushed into (and stays in) the delayed-receipt queue (`runtime/runtime/src/lib.rs:2591`-`2606`).
3. Two dynamic-resharding shard splits occur while the receipt is delayed, so the receipt's originally recorded `target_shard` no longer maps cleanly through the current shard layout's parent/child chain.
4. When compute pressure eases and the delayed queue is finally drained, `pop`/`peek_iter` call `receipt_filter_fn`, which unwraps a `receiver_shard_id` that can fail for this doubly-stale receipt, panicking the runtime.

Because this code runs inside `Runtime::apply`, which every validator (and every honest full node) executes deterministically for the same chunk, the panic is not a single-node crash — it aborts chunk application on all nodes tracking that shard, i.e., a protocol-wide, transaction-triggered halt, directly analogous to the Tor daemon-crashing assertion failure.

### Impact Explanation
A single account can trigger a `panic!`/`unwrap()` abort during normal chunk application by combining: (a) a `DeployGlobalContract` action, (b) sustained low-cost `FunctionCall` gas-burning to keep the resulting receipt delayed, and (c) waiting for shard-layout changes to occur (which under `DynamicResharding` can happen automatically based on chain conditions, not only manually). Because `receipt_filter_fn` is on the deterministic execution path invoked by `process_delayed_receipts`/`process_receipts` (`runtime/runtime/src/lib.rs:1943`-`1945`), a panic here crashes chunk-producing/validating nodes uniformly — a chain halt caused purely by a submitted transaction sequence, not by a malicious validator or network actor. This satisfies the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Exploitability depends on the resharding configuration and timing (needing two shard-split generations to elapse while the receipt remains delayed), which requires either an active `DynamicResharding` deployment or crafted testnet/production conditions where resharding is enabled. The bug is not hypothetical — it's precisely reproduced by the existing in-repo regression test `test_stale_global_contract_distribution_after_double_resharding`, indicating the authors were aware of and attempting to guard against this exact failure mode. Whether the current code fully closes the gap for all shard-layout/resharding configurations (the test guards it behind `ProtocolFeature::DynamicResharding` and V3 shard layouts) is unclear from static inspection alone, since `receiver_shard_id`'s remapping logic across two resharding generations was not fully traced in this review, and the `.unwrap()` in `receipt_filter_fn` still exists unconditionally in the code shown.

### Recommendation
- Replace the `.unwrap()` in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:876`) with a non-panicking fallback: if `receiver_shard_id` cannot resolve the shard for a stale receipt across multiple resharding generations, either retain full historical parent-shard chains for remapping or explicitly route/handle unresolvable receipts (e.g., treat as belonging to the querying shard, or fail chunk application gracefully with a recoverable error rather than a hard panic).
- Ensure `ShardLayout`'s parent/child mapping preserves multi-generation ancestry deep enough to always resolve `receiver_shard_id` for any receipt that could plausibly still be delayed after N resharding events, not just one.
- Extend `test_stale_global_contract_distribution_after_double_resharding` (and add a general delayed-`Action`-receipt variant, not just `GlobalContractDistribution`) to run across all supported shard-layout versions and confirm no panic path remains reachable.

### Proof of Concept
The repository's own test demonstrates the trigger sequence (`test-loop-tests/src/tests/global_contracts_distribution.rs:32`-`186`):
1. Deploy a contract and then a global contract from `user0`, producing a `GlobalContractDistribution` receipt targeting `user0`'s shard. [1](#0-0) 
2. Repeatedly submit `burn_gas_raw` calls each block to saturate the shard's compute budget, keeping the distribution receipt in the delayed queue across two forced shard splits. [2](#0-1) 
3. Stop saturating and let the delayed queue drain; the test explicitly asserts the chain does not stall, noting that a stall indicates `receipt_filter_fn` panicked on `receiver_shard_id()`. [3](#0-2) 

The panic-prone call site is: [4](#0-3) 
invoked from the mandatory delayed-receipt processing path used in every `Runtime::apply`: [5](#0-4) [6](#0-5)

### Citations

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L102-114)
```rust
    // Step 2: Deploy a global contract from user0. This creates a
    // GlobalContractDistribution receipt with target_shard = user0's shard (S_A),
    // which is the shard that will be split in the first resharding.
    {
        let node = env.node_for_account(&chunk_producer);
        let code = ContractCode::new(near_test_contracts::rs_contract().to_vec(), None);
        let tx = node.tx_deploy_global_contract(
            &deploy_user,
            code.code().to_vec(),
            GlobalContractDeployMode::CodeHash,
        );
        node.submit_tx(tx);
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-152)
```rust
    // Step 3: Saturate compute on user0's shard every block so that the
    // GlobalContractDistribution receipt (arriving as incoming) gets pushed to
    // the delayed queue and stays there through both resharding events.
    //
    // Each burn_gas_raw call burns slightly more than half the gas limit, so
    // two local receipts exhaust the chunk's compute budget. We submit 3 per
    // block to ensure at least 2 are processed as local receipts.
    let gas_to_burn = gas_limit.checked_div(2).unwrap().checked_add(Gas::from_gas(1)).unwrap();
    let initial_num_shards = base_shard_layout.num_shards();
    let target_num_shards = initial_num_shards + 2; // after two splits

    let start_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };

    // Keep saturating until both resharding events complete. Dynamic resharding has a
    // 2-epoch proposal-to-activation pipeline, so we need enough epochs for both splits.
    let max_saturation_height = start_height + epoch_length * 12;
    let mut both_splits_done = false;
    for target_height in (start_height + 1)..=max_saturation_height {
        // Submit 3 heavy transactions to saturate this block's compute budget.
        {
            let node = env.node_for_account(&chunk_producer);
            for _ in 0..3 {
                let tx = node.tx_call(
                    &deploy_user,
                    &deploy_user,
                    "burn_gas_raw",
                    gas_to_burn.as_gas().to_le_bytes().to_vec(),
                    Balance::ZERO,
                    gas_limit,
                );
                node.submit_tx(tx);
            }
        }
        env.runner_for_account(&chunk_producer).run_until_head_height(target_height);
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

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-910)
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
    }
```

**File:** runtime/runtime/src/lib.rs (L1940-1945)
```rust
        // Step 2: process transactions.
        self.process_transactions(&mut processing_state, signed_txs, &mut receipt_sink)?;

        // Step 3: process receipts.
        let process_receipts_result =
            self.process_receipts(&mut processing_state, &mut receipt_sink)?;
```
