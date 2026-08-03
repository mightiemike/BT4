### Title
Sharded-execution `TOTAL_SUPPLY_AGGR_BASE_VAL` override lets a transaction's non-aggregator writes be computed from a bogus total-supply read, producing state that diverges from canonical (non-sharded) execution - (`File: aptos-move/aptos-vm/src/sharded_block_executor/aggr_overridden_state_view.rs`)

### Summary
`AggregatorOverriddenStateView::get_state_value` intercepts every read of `TOTAL_SUPPLY_STATE_KEY` during sharded execution and substitutes a fixed constant, `TOTAL_SUPPLY_AGGR_BASE_VAL = u128::MAX >> 1`, instead of the real total-supply value. [1](#0-0) 
The substitution is corrected only for the `total_supply` key itself, after the fact, by the aggregation/coordinator logic in `sharded_executor_service.rs`/`sharded_aggregator_service`, which recombines deltas relative to `TOTAL_SUPPLY_AGGR_BASE_VAL` back into the true aggregate. [2](#0-1) [3](#0-2) 

### Finding Description
The override is designed for the case where a transaction only *adds a delta* to total supply (mint/burn), because the delta arithmetic is independent of the base value used during execution and can be corrected after the fact by subtracting `TOTAL_SUPPLY_AGGR_BASE_VAL` and adding the real base value. The code's own TODO comment acknowledges this is a stop-gap: "we need this because after all the txns are executed, the proof checker expects the total_supply to read/written to the tree." [4](#0-3) 

However, nothing prevents a transaction from *reading* `coin::total_supply` (which resolves the aggregator state key) and branching control flow or computing values for **other** state keys based on that raw number (e.g., `if (supply > X) { write_to_some_other_resource(...) }`). Because `TOTAL_SUPPLY_AGGR_BASE_VAL` (`u128::MAX >> 1`, an astronomically large number) has no relation to the real circulating supply, any such comparison against realistic thresholds will always evaluate differently under sharded execution than it would under normal (non-sharded) VM execution, which reads the real supply value.

The post-execution correction in `sharded_aggregator_service`/`sharded_executor_service` only fixes up the write to the `total_supply` key itself; it has no way to detect or correct writes to *other* keys whose values were computed using the wrong intermediate read. Those writes are already baked into the transaction's committed write set by the time the coordinator does its total-supply-specific correction.

### Impact Explanation
If the sharded block executor is used for any authoritative execution path (rather than purely as an internal experimental/benchmarking harness), a block containing such a total-supply-branching transaction will produce a write set that differs from the write set the same block would produce under the standard (non-sharded) BlockSTM executor. This is exactly the kind of "hard-fork-only divergence" the review scope calls out: two conforming execution engines given the same input transaction disagree on the resulting ledger state because one substitutes a fictitious total-supply value mid-execution.

### Likelihood Explanation
Exploitability requires only an ordinary, unprivileged transaction/script that calls the standard `coin::supply`/aggregator read API and branches on the result — no special privileges are needed. The main open question, which I could not fully resolve from the indexed code, is whether the sharded block executor path (`aptos-move/aptos-vm/src/sharded_block_executor/*`) is actually used in Aptos mainnet's consensus-critical execution path for validators, or whether it is restricted to auxiliary/experimental deployments (e.g., horizontally-scaled remote execution, benchmarking). If it is only used off the consensus-critical path, the "authenticated response"/state-commitment impact is reduced to a correctness bug in a non-authoritative executor rather than a state-commitment divergence on mainnet.

### Recommendation
- Do not allow the base-value substitution to leak into general Move execution semantics; either (a) make the aggregator override transparent only to the internal aggregator delta machinery (never surfaced through a plain state read visible to Move bytecode), or (b) disallow/detect transactions that read `total_supply` for direct value comparisons when running under the sharded executor, falling back to non-sharded execution for such transactions.
- Extend the post-aggregation correction pass to detect (or reject) any transaction output whose write set depends on the substituted total-supply value beyond simple delta application, or re-execute total-supply-dependent transactions against the corrected value.
- Confirm and document whether the sharded block executor is ever used as an authoritative consensus execution path; if so, add a conformance test comparing sharded vs. non-sharded execution results for transactions that branch on `coin::supply`.

### Proof of Concept
1. Deploy/execute a Move script under the sharded block executor that calls `coin::supply<AptosCoin>()` (or equivalent aggregator read of `TOTAL_SUPPLY_STATE_KEY`) and branches, e.g.:
   ```
   let supply = option::extract(&mut coin::supply<AptosCoin>());
   if (supply > 1000000000000) {
       // path A: writes resource R with value V_A
   } else {
       // path B: writes resource R with value V_B
   }
   ```
2. Under sharded execution, `AggregatorOverriddenStateView::get_state_value` returns `TOTAL_SUPPLY_AGGR_BASE_VAL = u128::MAX >> 1` (an astronomically large number) for the `total_supply` read, forcing the script down path A regardless of the real circulating supply. [5](#0-4) 
3. Under standard (non-sharded) execution of the identical transaction, the real total supply (far smaller than `u128::MAX >> 1`) is read, forcing path B.
4. The coordinator's post-aggregation correction step only rewrites the `total_supply` write set entry itself; the divergent write to resource `R` (`V_A` vs `V_B`) is never reconciled, producing two different, both "valid," write sets for the same transaction depending on which executor produced them.

### Citations

**File:** aptos-move/aptos-vm/src/sharded_block_executor/aggr_overridden_state_view.rs (L14-50)
```rust
pub const TOTAL_SUPPLY_AGGR_BASE_VAL: u128 = u128::MAX >> 1;
#[derive(Clone)]
pub struct AggregatorOverriddenStateView<'a, S> {
    base_view: &'a S,
    total_supply_aggr_base_val: u128,
}

impl<'a, S: StateView + Sync + Send> AggregatorOverriddenStateView<'a, S> {
    pub fn new(base_view: &'a S, total_supply_aggr_base_val: u128) -> Self {
        Self {
            base_view,
            total_supply_aggr_base_val,
        }
    }

    fn total_supply_base_view_override(&self) -> Result<Option<StateValue>> {
        Ok(Some(StateValue::new_legacy(
            bcs::to_bytes(&self.total_supply_aggr_base_val)
                .unwrap()
                .into(),
        )))
    }
}

impl<S: StateView + Sync + Send> TStateView for AggregatorOverriddenStateView<'_, S> {
    type Key = StateKey;

    fn get_state_value(&self, state_key: &StateKey) -> Result<Option<StateValue>> {
        if *state_key == *TOTAL_SUPPLY_STATE_KEY {
            // TODO: Remove this when we have aggregated total supply implementation for remote
            //       sharding. For now we need this because after all the txns are executed, the
            //       proof checker expects the total_supply to read/written to the tree.
            self.base_view.get_state_value(state_key)?;
            return self.total_supply_base_view_override();
        }
        self.base_view.get_state_value(state_key)
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_executor_service.rs (L1-18)
```rust
// Copyright (c) Aptos Foundation
// Licensed pursuant to the Innovation-Enabling Source Code License, available at https://github.com/aptos-labs/aptos-core/blob/main/LICENSE

use crate::{
    block_executor::AptosVMBlockExecutorWrapper,
    sharded_block_executor::{
        aggr_overridden_state_view::{AggregatorOverriddenStateView, TOTAL_SUPPLY_AGGR_BASE_VAL},
        coordinator_client::CoordinatorClient,
        counters::{
            SHARDED_BLOCK_EXECUTION_BY_ROUNDS_SECONDS, SHARDED_BLOCK_EXECUTOR_TXN_COUNT,
            SHARDED_EXECUTOR_SERVICE_SECONDS,
        },
        cross_shard_client::{CrossShardClient, CrossShardCommitReceiver, CrossShardCommitSender},
        cross_shard_state_view::CrossShardStateView,
        messages::CrossShardMsg,
        ExecutorShardCommand,
    },
};
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs (L213-220)
```rust
        let mut sharded_output = self.get_output_from_shards()?;

        sharded_aggregator_service::aggregate_and_update_total_supply(
            &mut sharded_output,
            &mut global_output,
            state_view.as_ref(),
            self.global_executor.get_executor_thread_pool(),
        );
```
