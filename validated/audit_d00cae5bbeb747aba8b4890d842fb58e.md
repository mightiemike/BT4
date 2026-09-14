This confirms a concrete, documented, currently-unfixed analog in this codebase. Two host-side size checks exist for receipt payload size (`value_return`'s `max_length_returned_data` check and `promise_batch_action_deploy_contract`'s `max_contract_size` check), but the *actual serialized receipt* that gets built afterward (adding `output_data_receivers`, DataReceipt framing, action-receipt fields) is never re-checked against `max_receipt_size`. This is exactly the vm2 bug class: a cap enforced at one API entry point (`Buffer.alloc`-style checks) bypassed via a different path that reaches the same resource ceiling (`Buffer.concat`/`Buffer.from{length}}`-style downstream composition) without re-validation.### Title
Runtime accepts and permanently persists action/data receipts larger than `max_receipt_size`, bypassing the size cap the same way vm2's `bufferAllocLimit` was bypassed — ([File: runtime/runtime/src/verifier.rs])

### Summary
`max_receipt_size` (and `max_length_returned_data`) are meant to be a hard cap enforced on every receipt entering the state witness/chunk, analogous to vm2's `bufferAllocLimit` cap on Buffer allocations. Nearcore itself documents that this cap is bypassed on multiple receipt-construction paths: a size check is performed against the *payload* at one point (e.g. `value_return`'s check of `max_length_returned_data`, or the contract-size check on `promise_batch_action_deploy_contract`), but the checked-and-approved payload is then wrapped into a larger structure (a `DataReceipt`, or an `ActionReceipt` with `output_data_receivers` attached) whose total serialized size is never re-validated against `max_receipt_size`. The result — exactly like `Buffer.concat`/`Buffer.from({length})` bypassing vm2's `bufferAllocLimit` — is that the cap holds for the "narrow" API but not for the composed object that ultimately consumes the resource being capped.

### Finding Description
`ValidateReceiptMode::ExistingReceipt` in `runtime/runtime/src/verifier.rs:727-740` documents this explicitly: [1](#0-0) 

The three concrete bypass paths, each with a dedicated regression test acknowledging the bug is *currently unfixed* (tracked as near/nearcore#12606):

1. **`value_return` bypass** — `value_return` (`runtime/near-vm-runner/src/wasmtime_runner/logic.rs:4563-4614`) checks the returned value length against `max_length_returned_data` only: [2](#0-1) 
That checked value later becomes a `DataReceipt`. `validate_data_receipt` (`runtime/runtime/src/verifier.rs:772-785`) re-checks only `max_length_returned_data`, never `max_receipt_size`, even though the wrapping `Receipt`/`ReceiptEnum::Data` framing adds overhead: [3](#0-2) 
This is exercised end-to-end by `test_max_receipt_size_value_return`, whose comment states the receipt "should be rejected, but currently isn't because of a bug": [4](#0-3) 

2. **`output_data_receivers` bypass** — a receipt is built and validated at exactly `max_receipt_size`, then `output_data_receivers` are appended afterward, pushing it over the limit with no re-check, per `test_max_receipt_size_promise_return`: [5](#0-4) 

3. The generic action-receipt validator `validate_action_receipt` (`runtime/runtime/src/verifier.rs:742-770`) only validates input-data-dependency count, `refund_to`, and action contents — it never computes/bounds the receipt's total serialized (borsh) size: [6](#0-5) 

The test harness (`assert_oversized_receipt_occurred` / `receipt_is_oversized`, `test-loop-tests/src/tests/max_receipt_size.rs:350-429`) confirms via `borsh::object_length` that receipts genuinely exceeding `max_receipt_size` (4 MiB) are found in the chain's incoming receipt proofs after being fully accepted and executed. [7](#0-6) 

### Impact Explanation
`max_receipt_size` exists specifically to bound `ChunkStateWitness` size (documented ceiling ~21 MiB, see `docs/misc/state_witness_size_limits.md`): [8](#0-7) 
Because the cap is bypassable through the `value_return`/`output_data_receivers` paths, a single unprivileged contract call/transaction can produce a receipt persisted into the chain (and propagated cross-shard as an incoming receipt, and included in the chunk's state witness) whose true serialized size exceeds the protocol's documented hard limit. This directly threatens the invariant the limit was designed to guarantee — bounded witness size for stateless validation and cross-shard receipt transport — the same "the mitigation invariant does not hold" framing used in the vm2 advisory. Because both honest chunk producers and validators run the same (buggy) code path, this specific instance does not cause a *divergence* between honest nodes today (both accept it) — the finding is that the resource limit meant to protect witness/receipt-size budgets is provably violable, an unbounded (up to the underlying payload size, e.g. up to 4 MiB extra padding per call) resource-cap bypass reachable from a single unprivileged transaction, matching the "resource exhaustion / oversized state artifact" bug class in the report. nearcore's own comment class this as a known, still-open issue rather than a hypothetical.

### Likelihood Explanation
High reachability: an unprivileged, permissionless contract deployer/caller can trigger this with a single `FunctionCall` action calling `value_return` with a payload sized at `max_length_returned_data`, or by chaining `promise_then`/`promise_return` so `output_data_receivers` are attached after the receipt was built at the size limit — both reproduced by existing nearcore test-loop tests (`test_max_receipt_size_value_return`, `test_max_receipt_size_promise_return`) without any special privileges, validator status, or malicious-peer behavior.

### Recommendation
Compute the final serialized (borsh) size of the receipt *after* all mutations (data wrapping, `output_data_receivers` attachment) and re-validate against `max_receipt_size` before the receipt is accepted/persisted, rather than only checking the pre-wrap payload length against `max_length_returned_data` in `value_return` and the contract-size checks in `promise_batch_action_deploy_contract`. This mirrors the vm2 fix approach: enforce the cap at the point where the capped resource is finally realized, not only at narrower upstream call sites.

### Proof of Concept
Existing in-repo regression tests already demonstrate the bypass and are the most authoritative PoC:
- `test_max_receipt_size_value_return` (`test-loop-tests/src/tests/max_receipt_size.rs:216-267`): deploys a contract, calls `max_receipt_size_value_return_method` with `value_size = max_receipt_size (4_194_304)`, and asserts (via `assert_oversized_receipt_occurred`) that a receipt above `max_receipt_size` was accepted into the chain.
- `test_max_receipt_size_promise_return` (`test-loop-tests/src/tests/max_receipt_size.rs:130-207`): builds a receipt sized exactly at `max_receipt_size`, then triggers `output_data_receivers` attachment, pushing it over the limit, and confirms via the same assertion helper that the oversized receipt was accepted. [9](#0-8)

### Citations

**File:** runtime/runtime/src/verifier.rs (L732-740)
```rust
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

**File:** runtime/runtime/src/verifier.rs (L742-770)
```rust
fn validate_action_receipt(
    limit_config: &LimitConfig,
    receipt: VersionedActionReceipt,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ReceiptValidationError> {
    if receipt.input_data_ids().len() as u64 > limit_config.max_number_input_data_dependencies {
        return Err(ReceiptValidationError::NumberInputDataDependenciesExceeded {
            number_of_input_data_dependencies: receipt.input_data_ids().len() as u64,
            limit: limit_config.max_number_input_data_dependencies,
        });
    }

    if let Some(account_id) = receipt.refund_to() {
        AccountId::validate(account_id.as_ref()).map_err(|_| {
            ReceiptValidationError::InvalidRefundTo { account_id: account_id.to_string() }
        })?;
    }

    validate_actions_with_mode(
        limit_config,
        receipt.actions(),
        receiver,
        current_protocol_version,
        mode,
    )
    .map_err(ReceiptValidationError::ActionsValidation)
}
```

**File:** runtime/runtime/src/verifier.rs (L772-785)
```rust
/// Validates given data receipt. Checks validity of the length of the returned data.
fn validate_data_receipt(
    limit_config: &LimitConfig,
    receipt: &DataReceipt,
) -> Result<(), ReceiptValidationError> {
    let data_len = receipt.data.as_ref().map(|data| data.len()).unwrap_or(0);
    if data_len as u64 > limit_config.max_length_returned_data {
        return Err(ReceiptValidationError::ReturnedValueLengthExceeded {
            length: data_len as u64,
            limit: limit_config.max_length_returned_data,
        });
    }
    Ok(())
}
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L4576-4585)
```rust
    )?;
    let mut burn_cost = ParameterCost::ZERO;
    let num_bytes = return_val.len() as u64;
    if num_bytes > ctx.config.limit_config.max_length_returned_data {
        return Err(HostError::ReturnedValueLengthExceeded {
            length: num_bytes,
            limit: ctx.config.limit_config.max_length_returned_data,
        }
        .into());
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-214)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L215-267)
```rust
#[test]
fn test_max_receipt_size_value_return() {
    init_test_logger();

    let account = create_account_id("account0");
    let account_signer = create_user_test_signer(&account);
    let mut env = TestLoopBuilder::new()
        .enable_rpc()
        .add_user_account(&account, Balance::from_near(10_000))
        .build();

    // Deploy the test contract
    let deploy_contract_tx = SignedTransaction::deploy_contract(
        101,
        &account,
        near_test_contracts::rs_contract().into(),
        &account_signer,
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(deploy_contract_tx, Duration::seconds(5));

    let max_receipt_size = 4_194_304;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_value_return_method".into(),
        format!("{{\"value_size\": {}}}", max_receipt_size).into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(large_receipt_tx, Duration::seconds(5));

    // Make sure that the last promise in the DAG was called
    let assert_test_completed = SignedTransaction::call(
        103,
        account.clone(),
        account,
        &account_signer,
        Balance::ZERO,
        "assert_test_completed".into(),
        "".into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(assert_test_completed, Duration::seconds(5));

    assert_oversized_receipt_occurred(&env.validator());
}
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L422-429)
```rust
fn receipt_is_oversized(receipt: &Receipt, max_receipt_size: u64) -> bool {
    let receipt_size: u64 = borsh::object_length(receipt).unwrap().try_into().unwrap();
    if receipt_size > max_receipt_size {
        tracing::info!(%receipt_size, %max_receipt_size, "found receipt above max size");
        return true;
    }
    false
}
```

**File:** docs/misc/state_witness_size_limits.md (L16-18)
```markdown
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```
