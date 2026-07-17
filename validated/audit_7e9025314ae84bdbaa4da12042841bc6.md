### Title
Bandwidth Scheduler Enforces Outgoing `max_shard_bandwidth` But Has No Incoming Limit When Sender Shard Has Missing Chunks — (`runtime/runtime/src/bandwidth_scheduler/scheduler.rs`)

---

### Summary

The bandwidth scheduler is designed to guarantee that every shard both **sends** and **receives** at most `max_shard_bandwidth` bytes of receipts per chunk. The outgoing (send) limit is enforced via `sender_budget` in `try_grant_bandwidth`. The incoming (receive) limit is enforced via `receiver_budget` in the same function — but only for the **current height's grants**. When a sender shard has missing chunks, previously-granted receipts that were deferred arrive at the receiver in a burst at a later height, bypassing the `receiver_budget` check entirely. The receiver can then receive far more than `max_shard_bandwidth` bytes in a single chunk. This is explicitly acknowledged in the production code with a `TODO` marker and is confirmed by a test that asserts the violation occurs.

---

### Finding Description

`BandwidthScheduler` documents two symmetric invariants:

> - Every shard sends out at most `max_shard_bandwidth` bytes of receipts at every height.
> - Every shard receives at most `max_shard_bandwidth` bytes of receipts at every height. [1](#0-0) 

Both are enforced inside `try_grant_bandwidth` by decrementing `sender_budget` and `receiver_budget`: [2](#0-1) 

These budgets are initialized to `max_shard_bandwidth` at the start of every scheduling run: [3](#0-2) 

The production value of `max_shard_bandwidth` is **4,500,000 bytes** (4.5 MB), activated at protocol version 74: [4](#0-3) 

**The gap:** When a sender shard has a missing chunk, `calculate_is_link_allowed` correctly returns `false` for links originating from that sender, so no new bandwidth is granted on those links at that height: [5](#0-4) 

However, receipts that were **already sent** by that shard in previous heights (when bandwidth was legitimately granted) are still in transit. They arrive at the receiver when the sender's next non-missing chunk appears. At that same height, all other active senders are also sending up to `max_shard_bandwidth` bytes each to the same receiver. The receiver's `receiver_budget` for the current height only accounts for the current height's grants — it has no mechanism to account for the deferred burst from the recovering sender.

The production code explicitly documents this as an unresolved gap: [6](#0-5) 

---

### Impact Explanation

A receiver shard can receive `(K + N − 1) × max_shard_bandwidth` bytes in a single chunk, where K is the number of consecutive missing chunks on the sender and N is the total number of shards. With `max_shard_bandwidth` = 4.5 MB, even a single missed chunk on one sender (K=1) with 6 shards (N=6) yields up to 6 × 4.5 MB = 27 MB of incoming receipts in one chunk — 6× the intended limit.

The direct consequence is that the **state witness** for the affected chunk grows proportionally. If it exceeds `main_storage_proof_size_soft_limit` (4 MB in production at protocol version 79+), chunk validation fails. A sustained pattern of missing chunks on any sender shard can therefore cause repeated chunk validation failures on the receiver, degrading liveness.

---

### Likelihood Explanation

Missing chunks occur naturally due to network partitions and validator latency — they are not an exceptional condition. The simulator test uses a 10% missing-chunk probability to model realistic conditions and explicitly asserts that the incoming limit is violated: [7](#0-6) 

The integration test for the real bandwidth scheduler also asserts the same violation and is marked `#[ignore]` with a tracking issue (`#12836`): [8](#0-7) 

Any user who submits a volume of cross-shard transactions sufficient to fill the outgoing buffer of a shard that subsequently experiences missing chunks will trigger this condition without any privileged access.

---

### Recommendation

Implement the "Future improvement (a)" described in the scheduler module comment:

> Add proper handling of missing chunks on sender shard. The scheduler could look at how much was granted on other senders which have missing chunks and forbid producing too many receipts to a receiver. [9](#0-8) 

Concretely: persist, per link, the cumulative bandwidth granted while the sender had missing chunks. When computing the `receiver_budget` for a given height, subtract the outstanding deferred grants for all recovering senders targeting that receiver. This mirrors how `sender_budget` already tracks the outgoing side.

---

### Proof of Concept

The existing simulator test already demonstrates the violation deterministically. With 6 shards and 10% missing-chunk probability, `max_incoming` consistently exceeds `max_shard_bandwidth` (4.5 MB):

```
// Incoming max_shard_bandwidth is not respected! When a chunk is missing, the receipts that
// were sent previously will arrive later and they can mix with other incoming receipts, and the
// receiver can receive more than max_shard_bandwidth of receipts :/
// TODO(bandwidth_scheduler) - prevent shard from having too many incoming receipts
assert!(summary.max_incoming > summary.max_shard_bandwidth);
``` [10](#0-9) 

The `ReceiptSink::new` call in the runtime applies the granted bandwidth as the hard outgoing limit per shard pair, but there is no symmetric incoming cap applied when receipts are delivered to `process_incoming_receipts`: [11](#0-10) [12](#0-11) 

The incoming receipt list is processed without any size gate against `max_shard_bandwidth`, confirming that the invariant is enforced only on the grant side (outgoing) and not on the delivery side (incoming) when deferred receipts from missing-chunk senders arrive.

### Citations

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L10-12)
```rust
//! Bandwidth scheduler tries to ensure that:
//! - Every shard sends out at most `max_shard_bandwidth` bytes of receipts at every height.
//! - Every shard receives at most `max_shard_bandwidth` bytes of receipts at every height.
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L105-110)
```rust
//! The situation is worse when the chunks are missing on the sender shard. Receipts sent during
//! previous chunk's application are received when the next non-missing chunk on the sender shard
//! appears. This could be arbitrarily far into the future and in the meantime other shards could
//! send receipts to the same receiver, and the receiver could receive more receipts than it should.
//! This is harder to deal with, the current version of bandwidth scheduler doesn't have a mechanism
//! to prevent that.
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L112-115)
```rust
//! Future improvements:
//! a) Add proper handling of missing chunks on sender shard. The scheduler could look at how much
//!    was granted on other senders which have missing chunks and forbid producing too many receipts
//!    to a receiver.
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L313-321)
```rust
    /// Initialize sender and receiver budgets. Every shard can send and receive at most `max_shard_bandwidth`.
    fn init_budgets(&mut self) {
        for sender in self.shard_layout.shard_indexes() {
            self.sender_budget.insert(sender, self.params.max_shard_bandwidth);
        }
        for receiver in self.shard_layout.shard_indexes() {
            self.receiver_budget.insert(receiver, self.params.max_shard_bandwidth);
        }
    }
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L473-487)
```rust
        let sender_budget = self.sender_budget.get(&link.sender).copied().unwrap_or(0);
        let receiver_budget = self.receiver_budget.get(&link.receiver).copied().unwrap_or(0);

        if sender_budget < bandwidth || receiver_budget < bandwidth {
            // Sender or receiver can't send this much as they would go over the per-shard budget.
            return TryGrantOutcome::NotGranted;
        }

        // Ok, grant the bandwidth
        self.sender_budget.insert(link.sender, sender_budget - bandwidth);
        self.receiver_budget.insert(link.receiver, receiver_budget - bandwidth);
        self.decrease_allowance(link, bandwidth);
        self.grant_more_bandwidth(link, bandwidth);

        TryGrantOutcome::Granted
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L522-531)
```rust
        let sender_status_opt = shards_status.get(&sender_index);
        if let Some(sender_status) = sender_status_opt {
            if sender_status.last_chunk_missing {
                // The chunk on sender's shard is missing. Don't grant any bandwidth on links from a shard
                // that is missing and won't send out anything at this height.
                // Bandwidth scheduler calculates outgoing bandwidth for chunks that are currently
                // being applied. The sender's chunk is not being applied right now, so it wouldn't
                // send out any receipts, it'd be wasteful to grant bandwidth on this link.
                return false;
            }
```

**File:** core/parameters/res/runtime_configs/74.yaml (L1-5)
```yaml
# Bandwidth scheduler
max_shard_bandwidth: { old: 999_999_999_999_999, new: 4_500_000 }
max_single_grant: { old: 999_999_999_999_999, new: 4194304 }
max_allowance: { old: 999_999_999_999_999, new: 4_500_000 }
max_base_bandwidth: { old: 999_999_999_999_999, new: 100_000 }
```

**File:** runtime/runtime/src/bandwidth_scheduler/simulator.rs (L579-600)
```rust
/// 10% of chunks are missing. How will the scheduler behave?
#[test]
fn test_bandwidth_scheduler_simulator_missing_chunks() {
    let scenario = TestScenarioBuilder::new()
        .num_shards(6)
        .default_link_generator(|| Box::new(RandomReceiptSizeGenerator))
        .missing_chunk_probability(0.1)
        .build();
    let summary = run_scenario(scenario);
    assert!(summary.bandwidth_utilization > 0.7); // > 70% utilization
    assert!(summary.link_imbalance_ratio < 1.6); // < 60% difference on links
    assert!(summary.worst_link_estimation_ratio > 0.50); // 50% of estimated link throughput

    // Incoming max_shard_bandwidth is not respected! When a chunk is missing, the receipts that
    // were sent previously will arrive later and they can mix with other incoming receipts, and the
    // receiver can receive more than max_shard_bandwidth of receipts :/
    // TODO(bandwidth_scheduler) - prevent shard from having too many incoming receipts
    assert!(summary.max_incoming > summary.max_shard_bandwidth);

    // Outgoing max_shard_bandwidth is respected
    assert!(summary.max_outgoing <= summary.max_shard_bandwidth);
}
```

**File:** test-loop-tests/src/tests/bandwidth_scheduler.rs (L80-99)
```rust
#[ignore] // TODO: #12836
fn ultra_slow_test_bandwidth_scheduler_four_shards_random_receipts_missing_chunks() {
    let scenario = TestScenarioBuilder::new()
        .num_shards(5)
        .default_link_generator(|| Box::new(RandomReceiptSizeGenerator))
        .missing_chunk_probability(0.1)
        .build();
    let summary = run_bandwidth_scheduler_test(scenario, 2000);
    assert!(summary.bandwidth_utilization > 0.35); // 35% utilization
    assert!(summary.link_imbalance_ratio < 6.0); // < 500% difference on links
    assert!(summary.worst_link_estimation_ratio > 0.1); // 10% of estimated link throughput

    // Incoming max_shard_bandwidth is not respected! When a chunk is missing, the receipts that
    // were sent previously will arrive later and they can mix with other incoming receipts, and the
    // receiver can receive more than max_shard_bandwidth of receipts :/
    // TODO(bandwidth_scheduler) - prevent shard from having too many incoming receipts
    assert!(summary.max_incoming > summary.max_shard_bandwidth);

    // Outgoing max_shard_bandwidth is respected
    assert!(summary.max_outgoing <= summary.max_shard_bandwidth);
```

**File:** runtime/runtime/src/congestion_control.rs (L95-119)
```rust
        let outgoing_limit: HashMap<ShardId, OutgoingLimit> = apply_state
            .congestion_info
            .iter()
            .map(|(&shard_id, congestion)| {
                let other_congestion_control = CongestionControl::new(
                    apply_state.config.congestion_control_config,
                    congestion.congestion_info,
                    congestion.missed_chunks_count,
                );
                let gas_limit = if shard_id != apply_state.shard_id {
                    other_congestion_control.outgoing_gas_limit(apply_state.shard_id)
                } else {
                    // No gas limits on receipts that stay on the same shard. Backpressure
                    // wouldn't help, the receipt takes the same memory if buffered or
                    // in the delayed receipts queue.
                    Gas::MAX
                };

                let size_limit = bandwidth_scheduler_output
                    .granted_bandwidth
                    .get_granted_bandwidth(apply_state.shard_id, shard_id);

                (shard_id, OutgoingLimit { gas: gas_limit, size: size_limit })
            })
            .collect();
```

**File:** runtime/runtime/src/lib.rs (L2485-2518)
```rust
    fn process_incoming_receipts(
        &self,
        mut processing_state: &mut ApplyProcessingReceiptState,
        receipt_sink: &mut ReceiptSink,
        compute_limit: u64,
        validator_proposals: &mut Vec<ValidatorStake>,
    ) -> Result<(), RuntimeError> {
        let incoming_processing_start = std::time::Instant::now();
        let protocol_version = processing_state.protocol_version;
        if let Some(prefetcher) = &mut processing_state.prefetcher {
            // Prefetcher is allowed to fail
            _ = prefetcher.prefetch_receipts_data(&processing_state.incoming_receipts);
        }

        let mut prep_lookahead_iter = processing_state.incoming_receipts.iter();
        // Advance the preparation by one step (stagger it) so that we're preparing one interesting
        // receipt in advance.
        let mut next_schedule_after = schedule_contract_preparation(
            &mut processing_state.pipeline_manager,
            &processing_state.state_update,
            &mut prep_lookahead_iter,
        );

        processing_state.outcomes.reserve(processing_state.incoming_receipts.len());
        for receipt in processing_state.incoming_receipts {
            // Validating new incoming no matter whether we have available gas or not. We don't
            // want to store invalid receipts in state as delayed.
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(RuntimeError::ReceiptValidationError)?;
```
