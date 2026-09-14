### Title
Congestion-delayed `PromiseResume` can be silently dropped after its `PromiseYieldTimeout` fires, losing the legitimate resolution ([File: runtime/runtime/src/lib.rs])

### Summary
The external report describes options expiring while users cannot exercise them because an L2 sequencer outage delays inclusion of the exercise transaction past the option's fixed expiry. The reachable analog in this nearcore tree is NEAR's `PromiseYield`/`PromiseResume` mechanism: a yielded promise has a fixed `expires_at: BlockHeight` after which a synthetic timeout resume is generated and delivered, and once *any* resume (real or timeout) is processed for a `data_id`, the other is discarded. If the real resume is delayed by cross-shard congestion/bandwidth throttling past `expires_at`, the synthetic timeout can be processed first, and the legitimate resume data is later dropped — a direct receipt-loss / state-divergence analog to "expiring before being exercised."

### Finding Description
`PromiseYieldTimeout` entries carry `expires_at: BlockHeight` [1](#0-0) . Each chunk, `resolve_promise_yield_timeouts` walks the timeout queue in `expires_at` order and, for every entry whose `expires_at <= block_height`, synthesizes a `PromiseResume` receipt with `data: None` destined for the same account, and dequeues the timeout [2](#0-1) .

When a `PromiseResume` is processed in `process_receipt`, the logic is race-sensitive: if the resume is a timeout resume (`data.is_none()`) and the stored `PromiseYieldStatus` is `ResumeInitiated`, the timeout is cancelled and dropped (`Ok(None)`) [3](#0-2) . Otherwise, whichever resume (real or synthetic timeout) is processed first for a given `data_id` removes the parked `PromiseYield` receipt, clears its status, writes `ReceivedData`, and executes the yield receipt immediately [4](#0-3) . A second resume for the same `data_id` then finds no parked yield receipt and is silently ignored [5](#0-4) .

The status-based cancellation only protects against races if `ResumeInitiated` is durably recorded on the yielding account's shard *before* the timeout fires. But the action that actually triggers `promise_yield_resume` (and its resulting `PromiseResume` receipt) can originate from a different, possibly congested, shard. Cross-shard receipts carrying that resolution are subject to congestion-control/bandwidth-scheduler admission: `ReceiptSink::forward_or_buffer_receipt`/`try_forward` buffers a receipt instead of forwarding it whenever the receiver's outgoing gas/size limit is exhausted [6](#0-5) , and a fully congested receiving shard grants forwarding only to its single `allowed_shard`, with `Gas::ZERO` to everyone else [7](#0-6) . There is no bound tying this buffering delay to the yield timeout window — a receipt can remain buffered for an unbounded number of blocks while the shard stays congested.

Consequently, an ordinary, unprivileged actor can flood a shard with transactions/receipts to keep it (or an intermediate shard on the resume's path) congested long enough that the real `PromiseResume` receipt is still sitting in an outgoing buffer when `expires_at` is reached on the yielding shard. The local timeout then fires first (no cross-shard status update has arrived yet to set `ResumeInitiated` on the receiving side, since that flag is itself only set/visible once the relevant state update lands on the yielding shard), consumes the yield with `data: None`, and when the real, congestion-delayed `PromiseResume` finally arrives, it is discarded per the "second resume is ignored" behavior.

### Impact Explanation
This is a concrete receipt-loss / invalid-state-transition-acceptance scenario reachable purely by submitting transactions that induce shard congestion (no privileged role required): a contract relying on `promise_yield_resume` for cross-contract callback resolution (e.g., an escrow, auction, or option-like contract awaiting confirmation before finalizing a payout) can have its yield resolved with `None`/failure data even though the correct resolution receipt genuinely existed and was en route, purely because network/shard congestion delayed its delivery past the timeout. Depending on contract logic, this can silently drop the legitimate resolution and cause funds/logic to finalize incorrectly or become permanently stuck in the erroneous "timed out" branch — matching the report's core harm (a time-bounded, unresumable action being lost due to inclusion/delivery delay outside the user's control).

### Likelihood Explanation
Likelihood is bounded by two facts I could not fully verify from the indexed code: (1) exactly where/when `PromiseYieldStatus::ResumeInitiated` is set relative to the resume-triggering receipt's cross-shard journey (I found the check-site at `lib.rs:1554-1563` but not the write-site), and (2) the default `yield_timeout_length` value versus realistic congestion-induced buffering delays. Congestion-driven multi-block buffering is a documented, protocol-legal behavior (not a bug in the buffering itself), so the precondition (sustained congestion lasting through the yield timeout window) is plausible but requires sustained flooding, making this a real but non-trivial-to-trigger race rather than a one-transaction exploit.

### Recommendation
Verify where `PromiseYieldStatus::ResumeInitiated` is set and ensure it is set (or an equivalent “resume in flight” signal is propagated) on the yielding shard as soon as the resume is created, not only once delivered; alternatively, extend `expires_at` handling so a timeout is not finalized once a corresponding resume receipt is provably in flight (e.g., tracked via the outgoing buffer/bandwidth-request bookkeeping), or increase/parameterize `yield_timeout_length` relative to worst-case congestion-buffering delay so genuine resumes cannot be raced by timeouts under adversarial congestion.

### Proof of Concept
Conceptual (not executed): (1) Contract A on shard S1 creates a `PromiseYield` awaiting resolution from a cross-contract call whose resume will be dispatched from shard S2. (2) An attacker floods shard S2→S1 (or an intermediate hop) with high-congestion-gas transactions so `CongestionControl::is_fully_congested` holds and S2's outgoing limit to S1 stays at `Gas::ZERO`/no bandwidth grant except for `allowed_shard`, per `ReceiptSink::new`/`try_forward` [8](#0-7) , keeping the real `PromiseResume` buffered. (3) Once `block_height` passes the yield's `expires_at`, `resolve_promise_yield_timeouts` synthesizes and delivers a `data: None` `PromiseResume` on S1, which is processed by `process_receipt`, consuming the yield with failure data [9](#0-8) . (4) When congestion subsides and the real resume is finally forwarded, it arrives to find no parked yield receipt and is dropped, permanently losing the legitimate resolution data.

### Citations

**File:** protocol-model/spec/cross-shard-congestion.md (L91-93)
```markdown
- **`PromiseYieldTimeout` / `PromiseYieldIndices`** — `receipt.rs:1090`, `:1075` — the
  timeout-queue entry `{ account_id, data_id, expires_at: BlockHeight }` and its FIFO
  indices, ordered by `expires_at`.
```

**File:** protocol-model/spec/cross-shard-congestion.md (L160-176)
```markdown
Every outgoing receipt goes through `ReceiptSink::forward_or_buffer_receipt`
(`congestion_control.rs:162` → `:292`). It computes the receipt's receiver shard,
size, and congestion gas, then calls `try_forward` (`:403`):

1. If `size > max_receipt_size`, size is clamped to `max_receipt_size` for the limit
   comparison (bug workaround for oversized receipts, issue #12606, `:417`).
2. The receiver's `OutgoingLimit` is looked up; a missing entry defaults to
   `{ gas: Gas::MAX, size: 0 }` (`:439`) — since the bandwidth scheduler, a shard may
   send **zero** bytes on a link with no grant.
3. Under `ClampOutgoingGasAdmission` (PV 85) the *admission* gas is clamped to
   `allowed_shard_outgoing_gas` (`:443`), so a single very-expensive receipt cannot be
   blocked forever by the gas limit; pre-85 the full receipt gas is used.
4. Forward iff `forward_limit.gas >= admission_gas && forward_limit.size >= size`
   (`:451`); then the receipt is pushed to `outgoing_receipts` and the limit is
   decremented by the *actual* gas and size (`:453`). Otherwise it is returned
   `NotForwarded` and `buffer_receipt` (`:466`) pushes it onto the outgoing buffer for
   that shard, growing `own_congestion_info` by its size and buffered gas (`:486`).
```

**File:** protocol-model/spec/cross-shard-congestion.md (L200-205)
```markdown
to this shard next block:

- **Fully congested** (`congestion_level == 1.0`, `is_fully_congested`, `:95`): only
  the `allowed_shard` may send, and only `allowed_shard_outgoing_gas`; every other
  shard gets `Gas::ZERO` (`:83-89`). This is the "red light" that guarantees progress
  while stopping unbounded growth.
```

**File:** protocol-model/spec/cross-shard-congestion.md (L292-297)
```markdown
A `PromiseResume` receipt (`lib.rs:1436`) delivers the awaited data. If it is a
*timeout* resume (`data: None`) and the yield status is `ResumeInitiated`, it is
dropped because a real resume already exists (`lib.rs:1441-1444`). Otherwise, if the
parked yield receipt is found, the runtime removes it and its status, stores the
`ReceivedData`, and executes the yield receipt immediately (`lib.rs:1450-1498`); a
second resume for the same `data_id` finds nothing and is ignored (`lib.rs:1499`).
```

**File:** protocol-model/spec/cross-shard-congestion.md (L299-307)
```markdown
**Timeouts** are the last step of `process_receipts`:
`resolve_promise_yield_timeouts` (`lib.rs:2986`) walks the `PromiseYieldTimeout` queue
in `expires_at` order, stopping at the first entry with `expires_at >
block_height` (`:3019`) or once the compute/proof-size budget is hit (`:3003`). For
each expired entry whose yield still exists, it synthesizes a `PromiseResume` with
`data: None` destined for the same (local) account and forwards/buffers it
(`:3037-3074`); the timeout is then dequeued (`:3078`). The timeout resume and any
real resume are ordered to the same shard, so a late timeout after a real resume is
simply discarded.
```

**File:** runtime/runtime/src/lib.rs (L1554-1611)
```rust
            VersionedReceiptEnum::PromiseResume(data_receipt) => {
                if data_receipt.data.is_none() {
                    // This is a timeout resume. Check the status to see if the receipt has been resumed.
                    let status =
                        get_promise_yield_status(state_update, account_id, data_receipt.data_id)?;
                    if status == Some(PromiseYieldStatus::ResumeInitiated) {
                        // A non-timeout resume receipt has been sent, cancel the timeout.
                        return Ok(None);
                    }
                }

                // Received a new PromiseResume receipt delivering input data for a PromiseYield.
                // It is guaranteed that the PromiseYield has exactly one input data dependency
                // and that it arrives first, so we can simply find and execute it.
                if let Some(yield_receipt) =
                    get_promise_yield_receipt(state_update, account_id, data_receipt.data_id)?
                {
                    // Remove the receipt from the state
                    remove_promise_yield_receipt(state_update, account_id, data_receipt.data_id);

                    // Clear the PromiseYield status
                    remove_promise_yield_status(state_update, account_id, data_receipt.data_id);

                    // Clean up yield_id <-> data_id mappings if this was created by yield_create_with_id
                    if ProtocolFeature::YieldWithId.enabled(apply_state.current_protocol_version) {
                        if let Some(yield_id) = get_yield_id_for_data_id(
                            state_update,
                            account_id,
                            data_receipt.data_id,
                        )? {
                            remove_yield_id_mappings(
                                state_update,
                                account_id,
                                yield_id,
                                data_receipt.data_id,
                            );
                        }
                    }

                    // Save the data into the state keyed by the data_id
                    set_received_data(
                        state_update,
                        account_id.clone(),
                        data_receipt.data_id,
                        &ReceivedData { data: data_receipt.data.clone() },
                    );

                    // Execute the PromiseYield receipt. It will read the input data and clean it
                    // up from the state.
                    return self
                        .apply_action_receipt(
                            state_update,
                            apply_state,
                            pipeline_manager,
                            &yield_receipt,
                            receipt_sink,
                            instant_receipts,
                            validator_proposals,
```

**File:** runtime/runtime/src/congestion_control.rs (L99-118)
```rust
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
```
