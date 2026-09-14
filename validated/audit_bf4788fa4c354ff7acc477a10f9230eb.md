### Title
Congestion-control outgoing gas admission is checked against a clamped value but debited by the real (unclamped) cost, letting a single receipt blow through the per-shard outgoing gas budget - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`ReceiptSinkV2::try_forward` decides whether to forward or buffer an outgoing receipt by comparing the receiving shard's remaining `OutgoingLimit.gas` against an *admission* value that, once `ClampOutgoingGasAdmission` is active, is `gas.min(allowed_shard_outgoing_gas)` rather than the receipt's real congestion gas. If the (deflated) admission value passes the check, the code then debits the limit by the **real, unclamped** `gas` value. This mirrors the Numa root cause exactly: the gate is evaluated against a value that understates the true effect of the action, while the action itself is applied using the true, uncapped value - so the checked invariant ("a shard may forward at most `outgoing_gas_limit`/`allowed_shard_outgoing_gas` worth of receipts per chunk") can be defeated by a single receipt.

### Finding Description
In `try_forward`: [1](#0-0) 

```rust
let admission_gas = if ProtocolFeature::ClampOutgoingGasAdmission
    .enabled(apply_state.current_protocol_version)
{
    gas.min(apply_state.config.congestion_control_config.allowed_shard_outgoing_gas)
} else {
    gas
};

if forward_limit.gas >= admission_gas && forward_limit.size >= size {
    outgoing_receipts.push(receipt);
    forward_limit.gas = forward_limit.gas.saturating_sub(gas);   // <-- real gas, not admission_gas
    forward_limit.size -= size;
    ...
    Ok(ReceiptForwarding::Forwarded)
}
```

- The *check* uses `admission_gas`, which is capped at `allowed_shard_outgoing_gas` - a small, protocol-configured minimum meant only to guarantee that a fully congested "allowed shard" still receives *some* minimal amount of new work per chunk so the system cannot deadlock. [2](#0-1) 
- The *debit* uses the receipt's real congestion gas, `gas`, which is computed from prepaid execution/send fees and attached function-call gas via `action_receipt_congestion_gas`/`compute_receipt_congestion_gas`, with no cap other than the protocol-wide per-receipt gas ceilings (`max_total_prepaid_gas`, `max_gas_burnt`, which the changelog notes was raised up to 1 PGas). [3](#0-2) 
- Because the admission gate only ever compares against the small clamped `allowed_shard_outgoing_gas`, any receipt whose real gas is far larger than that minimum still passes the gate as long as `forward_limit.gas >= allowed_shard_outgoing_gas` - even if `forward_limit.gas` is far smaller than the receipt's real `gas`. The subsequent `saturating_sub(gas)` then consumes (or saturates to zero) the *entire* remaining outgoing-gas budget for that receiver shard in one step.
- This is the same pattern as the Numa bug: a safety threshold is validated against a value taken *before*/*independent of* the actual size of the action, and the action is then applied at its true, much larger size, defeating the invariant the check was meant to enforce.
- The project's own test explicitly documents the invariant this bypass violates - that no single receipt should reserve a meaningful share of a shard's whole outgoing congestion budget: [4](#0-3) 
That test only covers `DeterministicStateInit` receipts (bounded by `max_state_init_entries`/`max_actions_per_receipt`); it does not cover ordinary `FunctionCall` receipts whose congestion gas is driven by `attached_gas`, which can be pushed up to the protocol's per-receipt gas ceiling.
- The clamp was intentionally introduced (PR #15924, CHANGELOG) to stop a receipt whose real gas exceeds the per-shard outgoing limit from being permanently stuck in the buffer - but the fix only touched the *comparison*, not the *debit*, reintroducing the invariant violation it was trying to avoid. [5](#0-4) 

### Impact Explanation
The `allowed_shard_outgoing_gas` / per-shard `outgoing_gas_limit` mechanism is the core fairness/anti-deadlock guarantee of NEP-539 congestion control: it bounds how much new work any one shard can push into a congested receiver per chunk, and reserves a controlled minimum for the "allowed shard" so the system can make forward progress without any shard's queue growing unboundedly. [2](#0-1) 
By constructing a receipt (e.g., a batch of function calls) whose congestion gas is large relative to `allowed_shard_outgoing_gas` but still within the protocol's per-receipt gas ceiling, an unprivileged sender can have that single receipt admitted and can consume the receiver's *entire* remaining outgoing gas budget for the chunk - a "gas admission bypass" that defeats the intended cap the same way the Numa CF check was defeated by minting past the warning threshold in one call. This degrades the congestion-control safety property (bounded, fairly-shared incoming work for a receiver shard) that other shards' `outgoing_gas_limit` computations rely on. All validators compute this deterministically from the same protocol config and receipt contents, so it does not cause consensus divergence by itself, but it is a genuine, protocol-level bypass of a documented gas-admission invariant that is reachable from a single ordinary transaction/receipt.

### Likelihood Explanation
High reachability: any account can create a receipt (via a normal `FunctionCall`/promise batch) whose `attached_gas`/prepaid execution gas is large, and route it toward a shard that is the current "allowed shard" for that chunk (deterministically computable from `block_height + shard_index`). No special privileges, validator status, or race condition are required - the bypass triggers on every chunk where such a receipt is buffered and then considered for forwarding while `ClampOutgoingGasAdmission` (PV 85, active for all currently supported protocol versions since `MIN_SUPPORTED_PROTOCOL_VERSION = 83`... actually gated at v85) is enabled.

### Recommendation
Debit the limit by the same (clamped) value used in the admission check, i.e. `forward_limit.gas = forward_limit.gas.saturating_sub(admission_gas)`, or equivalently perform the check post-hoc against the real `gas` cost after admission, consistent with how `check_storage_stake` validates storage cost *after* applying account mutations elsewhere in the runtime. This restores the invariant that the amount debited from `OutgoingLimit.gas` never exceeds what was actually checked/authorized for forwarding.

### Proof of Concept
Not executable from the index alone; conceptually:
1. Configure/observe `allowed_shard_outgoing_gas` (small, e.g., a few Tgas) and note the current chunk's `allowed_shard` (`(block_height + shard_index) % num_shards`).
2. Submit a transaction that produces a receipt to that shard with real congestion gas `G` far larger than `allowed_shard_outgoing_gas` but ≤ the protocol per-receipt gas ceiling (e.g., attach near-max gas to a `FunctionCall`, or chain several actions so `action_receipt_congestion_gas` sums close to `max_total_prepaid_gas`).
3. When `ReceiptSinkV2::try_forward` runs for this receipt: `admission_gas = min(G, allowed_shard_outgoing_gas) = allowed_shard_outgoing_gas`; the check `forward_limit.gas >= admission_gas` passes even though `forward_limit.gas` may be much smaller than `G`.
4. The receipt is forwarded and `forward_limit.gas = forward_limit.gas.saturating_sub(G)`, consuming the entire remaining budget (saturating to `0`) in one receipt instead of the small `admission_gas` amount that was actually validated.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L443-463)
```rust
        let admission_gas = if ProtocolFeature::ClampOutgoingGasAdmission
            .enabled(apply_state.current_protocol_version)
        {
            gas.min(apply_state.config.congestion_control_config.allowed_shard_outgoing_gas)
        } else {
            gas
        };

        if forward_limit.gas >= admission_gas && forward_limit.size >= size {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "forwarding buffered receipt");
            outgoing_receipts.push(receipt);
            forward_limit.gas = forward_limit.gas.saturating_sub(gas);
            forward_limit.size -= size;
            stats.forwarded_receipts.entry(shard).or_default().add_receipt(size, gas);

            Ok(ReceiptForwarding::Forwarded)
        } else {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "not forwarding buffered receipt");
            Ok(ReceiptForwarding::NotForwarded(receipt))
        }
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L716-725)
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
```

**File:** core/parameters/src/config.rs (L180-187)
```rust
    /// How much gas the chosen allowed shard can send to a 100% congested shard.
    ///
    /// This amount is the absolute minimum of new workload a congested shard has to
    /// accept every round. It ensures deadlocks are provably impossible. But in
    /// ideal conditions, the gradual reduction of new workload entering the system
    /// combined with gradually limited forwarding to congested shards should
    /// prevent shards from becoming 100% congested in the first place.
    pub allowed_shard_outgoing_gas: Gas,
```

**File:** runtime/runtime/src/tests/apply.rs (L6929-6937)
```rust
/// The worst state-init receipt validation accepts must not reserve a meaningful
/// share of the shard's entire outgoing congestion budget.
///
/// Sized as the worst case a transaction can produce, which is all three limits
/// at once: `max_actions_per_receipt` state-init actions, their entries summing
/// to `max_state_init_entries`, padded out to `max_transaction_size`. Spreading
/// the entries across the whole action budget is what a per-action limit failed
/// to bound, so the receipt is validated here too: the bound is only meaningful
/// for a receipt that would actually be admitted.
```

**File:** CHANGELOG.md (L61-61)
```markdown
* Clamp the gas admission check used when forwarding buffered receipts under congestion control to `allowed_shard_outgoing_gas`, so a receipt whose gas exceeds the per-shard outgoing budget can still be forwarded when the shard is in the "allowed" set. ([#15924](https://github.com/near/nearcore/pull/15924))
```
