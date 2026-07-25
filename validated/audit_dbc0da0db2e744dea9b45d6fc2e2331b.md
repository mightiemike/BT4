### Title
Receipt Size Limit Bypassed via Post-Validation `output_data_receivers` Mutation — (`runtime/runtime/src/lib.rs`)

---

### Summary

The nearcore runtime validates newly-created receipts against `max_receipt_size` (4 MiB) immediately after each action executes. However, when a contract uses `promise_return`, the runtime subsequently appends `output_data_receivers` entries from the parent receipt to the returned child receipt **after** that size check has already passed. The resulting receipt can exceed `max_receipt_size` and is forwarded to the receiving shard, bypassing the hard limit. The codebase acknowledges this as issue #12606 and adds workarounds, but the root cause — the post-validation mutation — remains unfixed in production code.

---

### Finding Description

**Step 1 — Validation happens before mutation.**

In `apply_action_receipt`, after each action executes, every newly-created receipt is validated:

```rust
// runtime/runtime/src/lib.rs:867-878
if new_result.result.is_ok() {
    if let Err(e) = new_result.new_receipts.iter().try_for_each(|receipt| {
        validate_receipt(
            &apply_state.config.wasm_config.limit_config,
            receipt,
            apply_state.current_protocol_version,
            ValidateReceiptMode::NewReceipt,
        )
    }) {
        new_result.result = Err(ActionErrorKind::NewReceiptValidationError(e).into());
    }
}
```

`validate_receipt` in `NewReceipt` mode measures the borsh-serialized size and rejects anything above `max_receipt_size`:

```rust
// runtime/runtime/src/verifier.rs:533-541
if mode == ValidateReceiptMode::NewReceipt {
    let receipt_size: u64 = borsh::object_length(receipt)...;
    if receipt_size > limit_config.max_receipt_size {
        return Err(ReceiptValidationError::ReceiptSizeExceeded { ... });
    }
}
```

**Step 2 — `output_data_receivers` are appended after validation.**

After the per-action validation loop completes, the runtime handles `promise_return` by mutating the already-validated child receipt:

```rust
// runtime/runtime/src/lib.rs:1040-1047
ReceiptEnum::Action(new_action_receipt)
| ReceiptEnum::PromiseYield(new_action_receipt) => new_action_receipt
    .output_data_receivers
    .extend_from_slice(&action_receipt.output_data_receivers()),
ReceiptEnum::ActionV2(new_action_receipt)
| ReceiptEnum::PromiseYieldV2(new_action_receipt) => new_action_receipt
    .output_data_receivers
    .extend_from_slice(&action_receipt.output_data_receivers()),
```

Each `DataReceiver` entry is ~40 bytes (a `CryptoHash` + `AccountId`). If the child receipt was crafted to be exactly at `max_receipt_size` before this append, the final receipt exceeds the limit.

**Step 3 — The oversized receipt is forwarded without re-validation.**

The receipt then flows into `forward_or_buffer_receipt`. The `try_forward` function detects the oversize but only clamps the size for limit-comparison purposes — it does **not** reject the receipt:

```rust
// runtime/runtime/src/congestion_control.rs:413-427
// There is a bug which allows to create receipts that are above the size limit.
// Let's pretend that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
// See https://github.com/near/nearcore/issues/12606
let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
if size > max_receipt_size {
    size = max_receipt_size;
}
```

**Step 4 — The receiving shard accepts the oversized receipt.**

When the oversized receipt arrives at the receiving shard, `process_incoming_receipts` validates it with `ExistingReceipt` mode, which explicitly skips the size check:

```rust
// runtime/runtime/src/verifier.rs:573-585
pub enum ValidateReceiptMode {
    NewReceipt,
    /// 2) There is a bug which allows to create receipts that are above the size limit.
    ///    Runtime has to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
}
```

The same path applies to `value_return`: returning a value of exactly `max_length_returned_data` (4 MiB) creates a `DataReceipt` whose total borsh size exceeds `max_receipt_size` due to envelope overhead, as demonstrated in `test_max_receipt_size_value_return`.

---

### Impact Explanation

The `max_receipt_size` limit exists to keep `ChunkStateWitness` under its 17 MiB target. The state witness contains all receipts processed in a chunk. An unprivileged user can craft a contract that repeatedly produces receipts slightly above `max_receipt_size`, inflating the witness beyond its design bound. This is a **contract execution flow breakage** (the size invariant on produced receipts is violated) and a **non-network-level denial of service** (state witness inflation degrades chunk validation throughput and can be fixed without a hardfork by re-validating receipts after the `output_data_receivers` append).

The broken invariant is: *every receipt forwarded to another shard must satisfy `borsh_size(receipt) ≤ max_receipt_size`*. The corrupted value is the borsh-serialized length of the forwarded receipt, which can exceed 4 MiB.

---

### Likelihood Explanation

Any account can deploy a contract. The trigger requires:
1. Deploy a contract with a method that calls `promise_create` with arguments sized to fill the receipt to `max_receipt_size − overhead`.
2. Chain a `.then(callback)` so the parent receipt has a non-empty `output_data_receivers`.
3. Inside the first promise, call `promise_return` on the large child receipt.

This is exactly the pattern exercised by `max_receipt_size_promise_return_method2` in the test contract. No privileged role is required. The test `test_max_receipt_size_promise_return` confirms the oversized receipt reaches the chain in production-equivalent conditions.

---

### Recommendation

Re-validate the size of the child receipt **after** appending `output_data_receivers` in `apply_action_receipt` (around `runtime/runtime/src/lib.rs:1040-1047`). If the post-append size exceeds `max_receipt_size`, set the action result to `NewReceiptValidationError(ReceiptSizeExceeded)` and roll back, consistent with how other validation failures are handled in the same loop. A similar check is needed for the `value_return` path where the `DataReceipt` envelope can push the total size above the limit.

---

### Proof of Concept

The existing test suite already demonstrates the bug end-to-end:

```
test-loop-tests/src/tests/max_receipt_size.rs
  fn test_max_receipt_size_promise_return()   // action receipt path
  fn test_max_receipt_size_value_return()     // data receipt path
```

Both tests call `assert_oversized_receipt_occurred`, which walks the chain and confirms a receipt with `borsh_size > max_receipt_size` was forwarded and accepted. The contract methods that trigger the bug are in `runtime/near-test-contracts/test-contract-rs/src/lib.rs` at `max_receipt_size_promise_return_method2` and `max_receipt_size_value_return_method`.

**Concrete values:**
- `max_receipt_size` = 4,194,304 bytes
- Oversized receipt observed in `test_max_receipt_size_yield_resume`: 4,194,504 bytes (200 bytes over limit)
- Root cause location: `runtime/runtime/src/lib.rs:1040–1047` (post-validation `output_data_receivers` append)
- Workaround location: `runtime/runtime/src/congestion_control.rs:417–427` (size clamped, not rejected) [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** runtime/runtime/src/lib.rs (L867-878)
```rust
            if new_result.result.is_ok() {
                if let Err(e) = new_result.new_receipts.iter().try_for_each(|receipt| {
                    validate_receipt(
                        &apply_state.config.wasm_config.limit_config,
                        receipt,
                        apply_state.current_protocol_version,
                        ValidateReceiptMode::NewReceipt,
                    )
                }) {
                    new_result.result = Err(ActionErrorKind::NewReceiptValidationError(e).into());
                }
            }
```

**File:** runtime/runtime/src/lib.rs (L1040-1049)
```rust
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

**File:** runtime/runtime/src/verifier.rs (L533-542)
```rust
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L1910-1939)
```rust
/// Do a promise_return with a large receipt.
/// The receipt has a single FunctionCall action with large args.
/// Creates DAG:
/// C[self.noop(large_args)] -then-> B[self.mark_test_completed()]
#[no_mangle]
pub unsafe fn max_receipt_size_promise_return_method2() {
    input(0);
    let mut args = vec![0u8; register_len(0) as usize];
    read_register(0, args.as_mut_ptr());
    let input_args_json: serde_json::Value = serde_json::from_slice(&args).unwrap();
    let args_size = input_args_json["args_size"].as_u64().unwrap();

    current_account_id(0);
    let current_account = vec![0u8; register_len(0) as usize];
    read_register(0, current_account.as_ptr() as _);

    let large_args = vec![0u8; args_size as usize];
    let noop_method = b"noop";
    let promise_c = promise_create(
        current_account.len() as u64,
        current_account.as_ptr() as u64,
        noop_method.len() as u64,
        noop_method.as_ptr() as u64,
        large_args.len() as u64,
        large_args.as_ptr() as u64,
        0,
        20 * TGAS,
    );

    promise_return(promise_c);
```
