I have sufficient evidence to write up the strongest analog now: the congestion-control gas accounting deliberately assigns **zero congestion gas** to `Data`, `PromiseYield`, `PromiseResume`, and `GlobalContractDistribution` receipts, mirroring the L1-data-fee report's pattern of a cost that is real but not reflected in the metering/reimbursement mechanism designed to bound resource usage.

### Title
Congestion control assigns zero "cost" to Data/PromiseYield/PromiseResume receipts, letting a single transaction's callback chain bypass the outgoing/incoming congestion backpressure meant to bound cross-shard receipt backlog - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
NEP-539 congestion control throttles cross-shard receipt forwarding based on a "congestion gas" value computed per receipt via `compute_receipt_congestion_gas`. For `Action`/`ActionV2` receipts this correctly sums prepaid exec/send fees and attached gas [1](#0-0) , but `Data`, `PromiseYield`, `PromiseResume`, and `GlobalContractDistribution` receipts are unconditionally priced at `Gas::ZERO`, with the comment explicitly acknowledging this is an "MVP" gap that "does not account for data receipts or postponed receipts" because looking up the true cost "would require reading the postponed receipt from the trie" [2](#0-1) . This is directly analogous to the xkeeper report: a real, non-trivial resource cost (extra trie storage for postponed receipts, extra delayed-queue/backlog processing on resume) is incurred by the protocol but is not reflected in the very fee/accounting mechanism (`CongestionInfo`) that exists specifically to throttle and price that resource consumption.

### Finding Description
Cross-contract calls create a callback `Action` receipt with an `input_data_id` and register the caller as an `output_data_receiver` on the callee's receipt [3](#0-2) . When the callee's `Data` receipt is produced, it is forwarded/buffered like any other receipt through `ReceiptSink::forward_or_buffer_receipt`, but because `compute_receipt_congestion_gas` returns zero for `Data` receipts, it contributes nothing to `buffered_receipts_gas` / `delayed_receipts_gas`, and therefore nothing to `congestion_level` in `CongestionControl::congestion_level` [4](#0-3) . Yet the receiving action receipt whose `input_data_id` it satisfies was already stored on disk as a **postponed receipt** (`set_postponed_receipt`, keyed by `PostponedReceiptId`) [5](#0-4) , consuming real trie storage that the congestion mechanism's `receipt_bytes`/memory metric is supposed to bound, and whose eventual execution cost (once all `input_data_ids` resolve) is never reserved against `outgoing_gas_limit`/`delayed_receipts_gas` the way an ordinary `Action` receipt's cost is reserved via `action_receipt_congestion_gas` [6](#0-5) . Likewise `PromiseResume` receipts — which the same comment notes "it is possible that a promise-resume ends up in the delayed receipts queue" — pass through the delayed queue at zero congestion cost [7](#0-6) . Because `compute_receipt_congestion_gas` is the sole gate used to decide `try_forward` vs. `buffer_receipt` and to compute `outgoing_gas_limit` for a would-be-congested receiver [8](#0-7) , a contract that fans out many cross-contract callbacks (each producing an `input_data_id`-bearing postponed receipt plus a zero-costed `Data`/`PromiseResume` receipt) can grow a receiver shard's postponed-receipt backlog and pending resumed-execution debt without that growth ever being visible to `delayed_receipts_gas`, `buffered_receipts_gas`, or `receipt_bytes` — the exact quantities `congestion_level`, `shard_accepts_transactions`, and `outgoing_gas_limit` are built from [9](#0-8) .

### Impact Explanation
This breaks the fee/cost-metering guarantee that NEP-539 congestion control is designed to provide: "bounded incoming work" via the fully-congested red-light and `reject_tx_congestion_threshold` gate is documented as an invariant, and it is asserted only against `delayed_receipts_gas`/`buffered_receipts_gas`/`receipt_bytes` [10](#0-9) . Since Data/PromiseYield/PromiseResume traffic is invisible to that accounting, a shard can accumulate a real, unbounded backlog of postponed receipts and pending resumed executions that never trips `congestion_level >= reject_tx_congestion_threshold`, so `shard_accepts_transactions` keeps admitting more work into the very shard that is actually saturated. This is a mismatched-fee-model DoS/backpressure-bypass analogous to the L1-data-fee report: the mechanism that is supposed to throttle real resource consumption charges zero for a class of receipts that do carry real, deferred cost, letting that cost accumulate outside the metering system that other shards rely on to decide how much more work to forward.

### Likelihood Explanation
This requires only ordinary cross-contract-call usage (`Promise::then`/callback chaining, or `promise_yield`/`promise_resume`), reachable by any unprivileged contract caller/transaction signer; no privileged or validator-only access is needed. The behavior is explicitly acknowledged in the code's own comments as an intentional-but-documented MVP simplification rather than an oversight, so it is a known, always-reachable gap rather than a rare edge case.

### Recommendation
Account for the deferred cost of `Data`/`PromiseResume` receipts (and the postponed action receipts they release) in `compute_receipt_congestion_gas`, e.g., by reserving congestion gas at the point the postponed receipt is stored (`set_postponed_receipt`) rather than trying to attribute it at the `Data` receipt itself, so the postponed-receipt backlog is reflected in `delayed_receipts_gas`/`receipt_bytes` and can trigger `shard_accepts_transactions` backpressure before the backlog becomes unbounded.

### Proof of Concept
1. Deploy a contract on shard A that, on each call, issues N `Promise::then` cross-contract calls to a contract on shard B, each creating an `input_data_id`-gated callback receipt (stored as a postponed receipt on shard A) [11](#0-10) .
2. Have many unprivileged accounts submit such transactions repeatedly; each callback's resolving `Data` receipt is priced at zero congestion gas by `compute_receipt_congestion_gas` [12](#0-11) , so `buffered_receipts_gas`/`delayed_receipts_gas` on shard A never rises to reflect the growing postponed-receipt count.
3. Observe (e.g., via `get_postponed_receipt_count_for_trie`) that the on-disk postponed-receipt count grows unboundedly [13](#0-12)  while `CongestionControl::congestion_level` / `shard_accepts_transactions` on shard A stays below `reject_tx_congestion_threshold`, so shard A keeps being admitted new transactions even though its real, deferred execution backlog keeps growing unmetered.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L292-325)
```rust
    pub(crate) fn forward_or_buffer_receipt(
        &mut self,
        receipt: Receipt,
        apply_state: &ApplyState,
        state_update: &mut TrieUpdate,
    ) -> Result<(), RuntimeError> {
        let shard = receipt.receiver_shard_id(&self.info.shard_layout)?;
        let size = compute_receipt_size(&receipt)?;
        let gas = compute_receipt_congestion_gas(&receipt, &apply_state.config)?;

        match ReceiptSinkV2::try_forward(
            receipt,
            gas,
            size,
            shard,
            &mut self.sink.outgoing_limit,
            &mut self.sink.outgoing_receipts,
            apply_state,
            &mut self.sink.stats,
        )? {
            ReceiptForwarding::Forwarded => (),
            ReceiptForwarding::NotForwarded(receipt) => {
                self.sink.buffer_receipt(
                    receipt,
                    size,
                    gas,
                    state_update,
                    shard,
                    apply_state.config.use_state_stored_receipt,
                )?;
            }
        }
        Ok(())
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L682-686)
```rust
    match receipt.versioned_receipt() {
        VersionedReceiptEnum::Action(action_receipt) => {
            // account for gas guaranteed to be used for executing the receipts
            action_receipt_congestion_gas(receipt, config, action_receipt.into())
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L687-713)
```rust
        VersionedReceiptEnum::Data(_data_receipt) => {
            // Data receipts themselves don't cost gas to execute, their cost is
            // burnt at creation. What we should count, is the gas of the
            // postponed action receipt. But looking that up would require
            // reading the postponed receipt from the trie.
            // Thus, the congestion control MVP does not account for data
            // receipts or postponed receipts.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::PromiseYield(_) => {
            // The congestion control MVP does not account for yielding a
            // promise. Yielded promises are confined to a single account, hence
            // they never cross the shard boundaries. This makes it irrelevant
            // for the congestion MVP, which only counts gas in the outgoing
            // buffers and delayed receipts queue.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::PromiseResume(_) => {
            // The congestion control MVP does not account for resuming a promise.
            // Unlike `PromiseYield`, it is possible that a promise-resume ends
            // up in the delayed receipts queue.
            // But similar to a data receipt, it would be difficult to find the cost
            // of it without expensive state lookups.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::GlobalContractDistribution(_) => Ok(Gas::ZERO),
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L716-735)
```rust
fn action_receipt_congestion_gas(
    receipt: &Receipt,
    config: &RuntimeConfig,
    action_receipt: VersionedActionReceipt,
) -> Result<Gas, IntegerOverflowError> {
    let prepaid_exec_gas =
        total_prepaid_exec_fees(config, &action_receipt.actions(), receipt.receiver_id())?
            .gas
            .checked_add(config.fees.fee(ActionCosts::new_action_receipt).exec_fee().gas)
            .ok_or(IntegerOverflowError)?;
    // account for gas guaranteed to be used for creating new receipts
    let prepaid_send_cost = total_prepaid_send_fees(config, &action_receipt.actions())?;
    let prepaid_gas = prepaid_exec_gas.checked_add_result(prepaid_send_cost.gas)?;

    // account for gas potentially used for dynamic execution
    let gas_attached_to_fns = total_prepaid_gas(&action_receipt.actions())?;
    let gas = gas_attached_to_fns.checked_add_result(prepaid_gas)?;

    Ok(gas)
}
```

**File:** protocol-model/spec/cross-shard-congestion.md (L129-147)
```markdown
### 3. Data dependencies: postponed receipts and promise results

An action receipt carries `input_data_ids` (`receipt.rs:605`) that must all be
satisfied before it runs. In `process_action_receipt` (`lib.rs:1529`) the runtime
counts how many of those ids are *not* yet in state (`has_received_data`,
`lib.rs:1546`); for each missing one it records a `PostponedReceiptId` link
(`lib.rs:1550`). If `pending_data_count == 0` it executes immediately
(`lib.rs:1561`); otherwise it stores a `PendingDataCount` and the receipt itself as a
**postponed receipt** (`lib.rs:1581`, `set_postponed_receipt`).

When a `Data` receipt arrives (`process_receipt`, `lib.rs:1322`) the runtime writes
`ReceivedData` (`lib.rs:1325`), then, if a `PostponedReceiptId` link exists, decrements
`PendingDataCount`; when it reaches 1→0 it removes the postponed receipt and executes
it (`lib.rs:1357-1391`). On execution, an action receipt's outputs become `Data`
receipts routed to each `output_data_receivers` entry (`lib.rs:1059`), so a
cross-contract call is: caller creates a callback action receipt with an
`input_data_id`, records itself as an `output_data_receiver` on the callee's receipt
(`receipt_manager.rs:111`, `create_action_receipt`), and the callee's return value is
turned into the `Data` receipt that resolves the dependency.
```

**File:** protocol-model/spec/cross-shard-congestion.md (L188-197)
```markdown
### 5. Congestion control math (NEP-539)

`CongestionControl::congestion_level` (`congestion_info.rs:44`) is the **max** of four
fractions, each clamped to [0,1] (`clamped_f64_fraction`, `:474`):

- incoming = `delayed_receipts_gas / max_congestion_incoming_gas` (`:331`)
- outgoing = `buffered_receipts_gas / max_congestion_outgoing_gas` (`:338`)
- memory = `receipt_bytes / max_congestion_memory_consumption` (`:345`)
- missed-chunks = `missed_chunks_count / max_congestion_missed_chunks`, but 0 when
  `missed_chunks_count <= 1` (`:68`)
```

**File:** protocol-model/spec/cross-shard-congestion.md (L352-360)
```markdown
## Invariants & failure modes

- **Bounded incoming work**: a fully congested shard grants `Gas::ZERO` to all
  senders except its `allowed_shard` (`congestion_info.rs:83`), and the bandwidth
  scheduler forbids all links into it except the allowed one (`scheduler.rs:535`);
  together these stop unbounded delayed-queue growth while guaranteeing one sender can
  always make progress. Asserted by the `test_missed_chunks_finalize`
  (`congestion_info.rs:814`) and the `test_*_congestion` tests
  (`:769` missed-chunks, `:621` memory, `:670` incoming, `:723` outgoing).
```

**File:** core/parameters/src/view.rs (L809-831)
```rust
/// The configuration for congestion control. More info about congestion [here](https://near.github.io/nearcore/architecture/how/receipt-congestion.html?highlight=congestion#receipt-congestion)
#[derive(Debug, serde::Serialize, serde::Deserialize, Clone, PartialEq)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct CongestionControlConfigView {
    /// How much gas in delayed receipts of a shard is 100% incoming congestion.
    ///
    /// See [`CongestionControlConfig`] for more details.
    pub max_congestion_incoming_gas: Gas,

    /// How much gas in outgoing buffered receipts of a shard is 100% congested.
    ///
    /// Outgoing congestion contributes to overall congestion, which reduces how
    /// much other shards are allowed to forward to this shard.
    pub max_congestion_outgoing_gas: Gas,

    /// How much memory space of all delayed and buffered receipts in a shard is
    /// considered 100% congested.
    ///
    /// See [`CongestionControlConfig`] for more details.
    pub max_congestion_memory_consumption: u64,

    /// How many missed chunks in a row in a shard is considered 100% congested.
    pub max_congestion_missed_chunks: u64,
```

**File:** nearcore/src/metrics.rs (L145-183)
```rust
fn get_postponed_receipt_count_for_shard(
    shard_id: ShardId,
    shard_layout: &ShardLayout,
    chunk_store: &ChunkStoreAdapter,
    block: &Block,
    store: &Store,
) -> Result<i64, anyhow::Error> {
    let shard_uid = ShardUId::from_shard_id_and_layout(shard_id, shard_layout);
    let chunk_extra = chunk_store.get_chunk_extra(block.hash(), &shard_uid)?;
    let state_root = chunk_extra.state_root();
    let storage = TrieDBStorage::new(store.trie_store(), shard_uid);
    let storage = Arc::new(storage);
    let flat_storage_chunk_view = None;
    let trie = Trie::new(storage, *state_root, flat_storage_chunk_view);
    get_postponed_receipt_count_for_trie(trie)
}

fn get_postponed_receipt_count_for_trie(trie: Trie) -> Result<i64, anyhow::Error> {
    let mut iter = trie.disk_iter()?;
    iter.seek_prefix([trie_key::col::POSTPONED_RECEIPT])?;
    let mut count = 0;
    for item in iter {
        let (key, value) = match item {
            Ok(item) => item,
            Err(err) => {
                tracing::trace!(target: "metrics", ?err, "trie-stats error when reading item");
                continue;
            }
        };
        if !key.is_empty() && key[0] != trie_key::col::POSTPONED_RECEIPT {
            tracing::trace!(target: "metrics", "trie-stats - stopping iteration as reached other col type");
            break;
        }
        count += 1;
        log_trie_item(&key, value);
    }
    tracing::trace!(target: "metrics", %count, "trie-stats postponed receipt count");
    Ok(count)
}
```
