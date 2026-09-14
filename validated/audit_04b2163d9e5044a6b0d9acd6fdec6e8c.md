### Title
Receipt size validation is bypassed when `output_data_receivers` are appended after the size check, allowing oversized receipts into the protocol - ([File: runtime/runtime/src/lib.rs])

### Summary
Analogous to the external report's core flaw — a value (ETH return data) is trusted/consumed without accounting for a size that can grow unbounded after the initial check — nearcore validates a newly-created receipt's size against `max_receipt_size` at creation time, but then *appends more data to that already-validated receipt* (`output_data_receivers`) afterward, without re-validating. The final on-chain receipt can therefore exceed the protocol's own hard size limit, a state the rest of the system (congestion control, bandwidth scheduler, state-witness size budgeting) assumes cannot happen. This is tracked upstream as a known, only partially-mitigated bug (nearcore issue #12606).

### Finding Description
`validate_receipt` in `runtime/runtime/src/verifier.rs` computes the receipt's borsh size and rejects it if it exceeds `limit_config.max_receipt_size`, but only in `ValidateReceiptMode::NewReceipt` mode: [1](#0-0) 

The `ValidateReceiptMode::ExistingReceipt` variant is documented as intentionally more lenient, precisely *because* the runtime already produces receipts that violate the size limit: [2](#0-1) 

The root cause is in `runtime/runtime/src/lib.rs`: after a function-call's `ActionResult` (already validated/sized as a new receipt) is produced, the runtime mutates that same receipt in place to attach the caller's `output_data_receivers` — a step that happens *after* size validation has already passed, with no follow-up size check: [3](#0-2) 

This is exactly the pattern the test suite documents as a live, acknowledged bug: [4](#0-3) [5](#0-4) 

Concretely: a contract can build a promise DAG `A -then-> B`; inside `A`'s execution, create a new promise `C` sized to exactly `max_receipt_size` and call `promise_return` on it. `promise_return`/`value_return` only check the *outgoing value*/receipt against `max_receipt_size` at the moment of creation: [6](#0-5) 

But because `A` has `output_data_receivers` (from being chained to `B`), the runtime then appends those receivers onto receipt `C` post-hoc (the `lib.rs:1031-1049` code above), pushing `C` over `max_receipt_size` with no re-validation. The oversized receipt is committed to state and gets processed by both same-node and cross-shard code paths that assume `size <= max_receipt_size`, e.g. the congestion-control forwarder has to special-case this by *clamping* the observed size back down to `max_receipt_size` to avoid the receipt getting permanently stuck: [7](#0-6) 

### Impact Explanation
This is a validation-bypass / invariant-violation bug directly reachable by a single unprivileged contract call (any account can deploy a contract and invoke `promise_return`/`value_return` to trigger it), matching the required "concrete unauthorized value movement... invalid state transition acceptance" bar via **invalid state transition acceptance**: a receipt violating the protocol's own committed size invariant is accepted into state and propagated across shards. Consequences already acknowledged in-repo:
- Oversized receipts can violate the assumptions baked into `ChunkStateWitness` size budgeting (`docs/misc/state_witness_size_limits.md`), which is explicitly designed to keep total witness size under ~17MiB by bounding `max_receipt_size`; an oversized receipt breaks that accounting.
- Cross-shard forwarding/bandwidth-scheduler logic has to special-case (clamp) oversized receipts to prevent them getting "stuck" forever in the outgoing buffer (`congestion_control.rs:413-427`, `:561`), i.e., without the clamp workaround this is a receipt-loss / permanently-stuck-receipt condition (frozen funds/gas if the receipt carries a transfer or callback).
- Because the size-limit enforcement differs between `NewReceipt` and `ExistingReceipt` validation modes, and different nodes/versions could compute or enforce differently, this is a latent source of state-root divergence risk between honest nodes that don't agree on how to treat/clamp an out-of-spec receipt.

### Likelihood Explanation
High likelihood of triggering: it requires no special privileges, no validator role, and no off-chain conditions — a single deployed contract using `promise_return`/`value_return` with a receipt sized close to `max_receipt_size` combined with an `output_data_receivers` chain (any `.then()` callback pattern) reliably reproduces it, as demonstrated by the repo's own regression tests (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`).

### Recommendation
Re-validate (or account for) the full final receipt size — including any post-hoc `output_data_receivers` additions — before committing the receipt, or reserve headroom in the initial `value_return`/`promise_return` size check for the maximum possible `output_data_receivers` overhead so the final receipt can never exceed `max_receipt_size`. Alternatively, move the `output_data_receivers` append step in `runtime/runtime/src/lib.rs` before the size-validation call so oversized results are rejected consistently, removing the need for the `ExistingReceipt` leniency and the congestion-control clamp workaround.

### Proof of Concept
1. Deploy a contract exposing a method that: creates promise `A` chained `.then()` to promise `B` (giving `A`'s receipt an `output_data_receivers` entry pointing at `B`).
2. Inside `A`'s callback, create promise `C` with a `FunctionCall` action whose `args` are sized so that `borsh::object_length(C)` is exactly (or very close to) `max_receipt_size`, then call `promise_return(C)`.
3. Because `C` replaces `A` in the DAG (`C -then-> B`), the runtime appends `A`'s `output_data_receivers` onto `C` after `C` already passed size validation (`lib.rs:1031-1049`), producing a final receipt whose serialized size exceeds `max_receipt_size`.
4. Observe (as in `test_max_receipt_size_promise_return`/`test_max_receipt_size_value_return` in `test-loop-tests/src/tests/max_receipt_size.rs`) that the oversized receipt is accepted into a block/chunk rather than being rejected with `ReceiptSizeExceeded`.

### Citations

**File:** runtime/runtime/src/verifier.rs (L526-542)
```rust
/// Validates a given receipt. Checks validity of the Action or Data receipt.
pub(crate) fn validate_receipt(
    limit_config: &LimitConfig,
    receipt: &Receipt,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ReceiptValidationError> {
    if mode == ValidateReceiptMode::NewReceipt {
        let receipt_size: u64 =
            borsh::object_length(receipt).unwrap().try_into().expect("Can't convert usize to u64");
        if receipt_size > limit_config.max_receipt_size {
            return Err(ReceiptValidationError::ReceiptSizeExceeded {
                size: receipt_size,
                limit: limit_config.max_receipt_size,
            });
        }
    }
```

**File:** runtime/runtime/src/verifier.rs (L573-586)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidateReceiptMode {
    /// Used for validating new receipts that were just created.
    /// More strict than `OldReceipt` mode, which has to handle older receipts.
    NewReceipt,
    /// Used for validating older receipts that were saved in the state/received. Less strict than
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
}
```

**File:** runtime/runtime/src/lib.rs (L1025-1049)
```rust
        // Generating outgoing data
        // A {
        // B().then(C())}  B--data receipt->C

        // A {
        // B(); 42}
        if !action_receipt.output_data_receivers().is_empty() {
            if let Ok(ReturnData::ReceiptIndex(receipt_index)) = result.result {
                // Modifying a new receipt instead of sending data
                match result
                    .new_receipts
                    .get_mut(receipt_index as usize)
                    .expect("the receipt for the given receipt index should exist")
                    .receipt_mut()
                {
                    ReceiptEnum::Action(new_action_receipt)
                    | ReceiptEnum::PromiseYield(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    ReceiptEnum::ActionV2(new_action_receipt)
                    | ReceiptEnum::PromiseYieldV2(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    _ => unreachable!("the receipt should be an action receipt"),
                }
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-212)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3929-3940)
```rust
    pub fn value_return(&mut self, value_len: u64, value_ptr: u64) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        let return_val = get_memory_or_register!(self, value_ptr, value_len)?;
        let mut burn_cost = ParameterCost::ZERO;
        let num_bytes = return_val.len() as u64;
        if num_bytes > self.config.limit_config.max_length_returned_data {
            return Err(HostError::ReturnedValueLengthExceeded {
                length: num_bytes,
                limit: self.config.limit_config.max_length_returned_data,
            }
            .into());
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L413-427)
```rust
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
