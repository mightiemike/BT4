### Title
Oversized receipt size is silently clamped (not rejected) in cross-shard forwarding accounting, causing outgoing byte budgets to be under-decremented - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
This is a plausible analog of the Chainlink `minAnswer`/`maxAnswer` bug class ("a bounded/clamped value is silently substituted for the real one and then trusted downstream as if it were accurate"). In `ReceiptSinkV2::try_forward`, a receipt whose true serialized size exceeds `max_receipt_size` has its `size` variable forcibly overwritten with `max_receipt_size` before being used both for the forwarding-limit *comparison* and for *decrementing* the per-shard outgoing bandwidth budget (`forward_limit.size -= size`). Just like the Chainlink feed continuing to report `minAnswer` instead of the real (out-of-range) price, the congestion-control/bandwidth accounting continues to use the clamped size instead of the real one, understating how much of the shard-to-shard byte budget was actually consumed.

### Finding Description
`ReceiptSinkV2::try_forward` receives the real receipt `size` as a parameter: [1](#0-0) 

```rust
fn try_forward(
    receipt: Receipt,
    gas: Gas,
    mut size: u64,
    ...
) -> Result<ReceiptForwarding, RuntimeError> {
    // There is a bug which allows to create receipts that are above the size limit. ...
    // Let's pretend that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
    // See https://github.com/near/nearcore/issues/12606
    let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
    if size > max_receipt_size {
        ...
        size = max_receipt_size;
    }
```

The comment itself acknowledges that oversized receipts can exist (`issue #12606`) — i.e., an unprivileged transaction sender can, via a contract call that generates a large cross-contract receipt (as exercised in `test-loop-tests/src/tests/max_receipt_size.rs`), cause a receipt whose real serialized size exceeds the configured `max_receipt_size`.

After the clamp, the *same, now-falsified* `size` variable is used to both gate forwarding and to decrement the shard's remaining budget: [2](#0-1) 

```rust
if forward_limit.gas >= admission_gas && forward_limit.size >= size {
    ...
    outgoing_receipts.push(receipt);
    forward_limit.gas = forward_limit.gas.saturating_sub(gas);
    forward_limit.size -= size;
    ...
}
```

Because `size` was overwritten with `max_receipt_size` (not the receipt's true size), `forward_limit.size` — the per-chunk, per-receiver "how many bytes I'm still allowed to forward to this shard" budget — is decremented by less than the actual number of bytes placed into `outgoing_receipts`. `outgoing_receipts_usual_size_limit` / `outgoing_receipts_big_size_limit` govern this same forwarding budget and are explicitly documented as bounding the total size of outgoing receipts to keep `source_receipt_proofs` (i.e., state-witness inputs) under control: [3](#0-2) 

Similarly, at bandwidth-request generation time this same clamp-to-`max_receipt_size` behavior is applied ("`:561`" per the architecture spec), meaning the discrepancy between the accounted size and the real size propagates into the bandwidth scheduler's request/grant bookkeeping as well: [4](#0-3) 

This mirrors the Chainlink bug pattern precisely: rather than rejecting/reverting when a value falls outside the expected/safe bound (`> max_receipt_size`), the code substitutes the boundary value and continues normal accounting as if that were the true value — corrupting the invariant the limit was designed to enforce.

### Impact Explanation
The `outgoing_receipts_usual_size_limit`/`big_size_limit` and bandwidth-grant `size` budgets exist specifically to bound how many bytes of receipts a shard forwards per chunk, which in turn bounds the size of `source_receipt_proofs` embedded in `ChunkStateWitness` for the receiving shard. Because the true byte cost of an oversized receipt is under-counted by the difference `(real_size − max_receipt_size)`, a chunk producer can push meaningfully more real outgoing bytes to a receiver shard within a single chunk than the size/bandwidth budget was intended to allow, since each additional oversized receipt only "costs" `max_receipt_size` against the budget while contributing its full real size to `outgoing_receipts`. Repeated across many oversized receipts in one chunk, this can inflate the real byte volume forwarded to a receiver shard beyond the limits that congestion control and the bandwidth scheduler are supposed to enforce, growing `source_receipt_proofs`/witness size beyond the intended safety margin that these limits exist to protect. Since the clamp is deterministic and identical for all honest nodes running the same protocol version, this is not itself a consensus-divergence bug, but it defeats the purpose of the size/bandwidth caps (an intentional protocol safety limit), which is the core "trusting an incorrect boundary value" failure mode described in the report, and can be leveraged by any unprivileged transaction sender who can trigger oversized cross-contract receipts (as already demonstrated to be possible per the linked issue #12606 and its regression test).

### Likelihood Explanation
Triggering an oversized receipt is directly reachable from an ordinary contract call — `test-loop-tests/src/tests/max_receipt_size.rs` demonstrates that a function call can generate a large receipt (test explicitly generates >5MB receipts to probe this limit) without any special privilege, and the code comment confirms this is a known, currently-open path ("There is a bug which allows to create receipts that are above the size limit," referencing issue #12606). The under-accounting occurs automatically any time such a receipt is buffered/forwarded, requiring no additional conditions beyond congestion (a shard with a non-`Gas::MAX`/limited `size` budget for the receiver).

### Recommendation
Do not reuse the clamped `size` for budget accounting. Keep the clamp (or an explicit rejection) for the *forwarding-limit comparison* only, but decrement `forward_limit.size` (and any downstream congestion/bandwidth-request accounting that reads this same variable) by the *actual* receipt size, or reject/queue-for-fix receipts whose real size exceeds `max_receipt_size` instead of silently treating them as if they were within bounds. At minimum, separate the "comparison size" from the "accounting size" so the true consumed bandwidth is always recorded, matching the report's recommendation to fail/handle out-of-range values explicitly rather than substituting an in-range boundary value and proceeding as if it were correct.

### Proof of Concept
1. Deploy a contract capable of generating a single receipt whose serialized size exceeds `max_receipt_size` (as done via `generate_large_receipt` in `test-loop-tests/src/tests/max_receipt_size.rs`, lines 46-81), targeting a shard whose forwarding budget (`OutgoingLimit.size`) is limited (i.e., under active congestion/bandwidth constraints rather than the same-shard `Gas::MAX`/big-limit case).
2. Submit repeated transactions producing such oversized receipts within the same chunk toward the same congested receiver shard.
3. Observe (via `own_congestion_info`/`stats.forwarded_receipts` accounting, or instrumented logging in `try_forward`) that `forward_limit.size` is decremented only by `max_receipt_size` per receipt while `outgoing_receipts` actually contains bytes exceeding that amount per receipt, allowing more real receipt bytes to be forwarded per chunk to the receiver shard than the shard's configured `outgoing_receipts_usual_size_limit`/bandwidth grant should permit — quantitatively verifiable by comparing `sum(real receipt sizes forwarded)` against the nominal budget consumed (`sum(clamped sizes)`).

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L403-427)
```rust
    fn try_forward(
        receipt: Receipt,
        gas: Gas,
        mut size: u64,
        shard: ShardId,
        outgoing_limit: &mut HashMap<ShardId, OutgoingLimit>,
        outgoing_receipts: &mut Vec<Receipt>,
        apply_state: &ApplyState,
        stats: &mut ReceiptSinkStats,
    ) -> Result<ReceiptForwarding, RuntimeError> {
        // There is a bug which allows to create receipts that are above the size limit. Receipts
        // above the size limit might not fit under the maximum outgoing size limit. Let's pretend
        // that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
        // See https://github.com/near/nearcore/issues/12606
        let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
        if size > max_receipt_size {
            tracing::debug!(
                target: "runtime",
                receipt_id=?receipt.receipt_id(),
                size,
                max_receipt_size,
                "try_forward observed a receipt with size exceeding the size limit",
            );
            size = max_receipt_size;
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L451-456)
```rust
        if forward_limit.gas >= admission_gas && forward_limit.size >= size {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "forwarding buffered receipt");
            outgoing_receipts.push(receipt);
            forward_limit.gas = forward_limit.gas.saturating_sub(gas);
            forward_limit.size -= size;
            stats.forwarded_receipts.entry(shard).or_default().add_receipt(size, gas);
```

**File:** core/parameters/src/config.rs (L219-227)
```rust
    /// The standard size limit for outgoing receipts aimed at a single shard.
    /// This limit is pretty small to keep the size of source_receipt_proofs under control.
    /// It limits the total sum of outgoing receipts, not individual receipts.
    pub outgoing_receipts_usual_size_limit: u64,

    /// Large size limit for outgoing receipts to a shard, used when it's safe
    /// to send a lot of receipts without making the state witness too large.
    /// It limits the total sum of outgoing receipts, not individual receipts.
    pub outgoing_receipts_big_size_limit: u64,
```

**File:** protocol-model/spec/cross-shard-congestion.md (L372-374)
```markdown
- **Oversized-receipt workaround**: receipts above `max_receipt_size` are treated as
  exactly `max_receipt_size` for both forwarding limits (`congestion_control.rs:417`)
  and bandwidth requests (`:561`) so they cannot get permanently stuck (issue #12606).
```
