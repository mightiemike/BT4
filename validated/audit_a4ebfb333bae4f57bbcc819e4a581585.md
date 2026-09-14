This confirms a strong, code-documented analog. `validate_receipt` with `ValidateReceiptMode::NewReceipt` enforces `max_receipt_size` at `runtime/runtime/src/verifier.rs:687-696`, checked right after `apply_action` produces a new receipt (`runtime/runtime/src/lib.rs:871` per the spec). But post-processing steps that run *after* that per-action validation — specifically the output-data attachment step at `runtime/runtime/src/lib.rs:1146-1191` (appending `output_data_receivers` into an already-validated receipt when the action returns `ReturnData::ReceiptIndex`, or appending a `Data` receipt carrying the returned value) — mutate/create the receipt's size *after* the size gate ran, exactly mirroring the joserfc pattern of an alternate/later path bypassing a size check enforced elsewhere. This is a pre-existing, still-open, developer-acknowledged bug (nearcore issue #12606), with `ValidateReceiptMode::ExistingReceipt` explicitly documented as a workaround "until the receipt size limit bug is fixed" (`runtime/runtime/src/verifier.rs:732-738`).

### Title
Post-validation `output_data_receivers`/value-return mutation lets a `FunctionCall` produce receipts exceeding `max_receipt_size` - (File: runtime/runtime/src/lib.rs)

### Summary
`validate_receipt(..., ValidateReceiptMode::NewReceipt)` enforces `max_receipt_size` (`runtime/runtime/src/verifier.rs:687-696`) when each freshly created receipt is validated during action execution. However, after that validation point, `apply_action_receipt` unconditionally appends `output_data_receivers` to an already-created receipt (when the function call returns `ReturnData::ReceiptIndex`) or appends new `Data` receipts carrying the return value, at `runtime/runtime/src/lib.rs:1146-1191`. This mutation/creation happens strictly after the size check ran, so a receipt can end up larger than `max_receipt_size` while never being caught by the size gate — analogous to the joserfc bug where an alternate/later code path (RFC7797 unencoded payload assignment) bypasses a size check enforced on the normal path.

### Finding Description
`apply_action_receipt` executes each action, and for every new receipt created during that action `validate_receipt(new_receipt, ..., ValidateReceiptMode::NewReceipt)` is invoked, which checks `borsh::object_length(receipt) <= max_receipt_size` (`verifier.rs:687-696`). This is the "normal path" size gate, directly analogous to joserfc's compact/flattened-JSON `validate_payload_size` check.

After all actions for the current receipt finish and results are merged, a separate block at `runtime/runtime/src/lib.rs:1146-1191` inspects `action_receipt.output_data_receivers()`:
- If the function call's return value was `ReturnData::ReceiptIndex(receipt_index)`, the code reaches into `result.new_receipts[receipt_index]` — a receipt that was already validated and passed the size check — and does `new_action_receipt.output_data_receivers.extend_from_slice(...)`, growing it further.
- Otherwise, it constructs brand-new `Data` receipts carrying the returned value, which are never run through `validate_receipt` at all before being queued into `result.new_receipts`.

Because this mutation/creation occurs after the per-receipt `NewReceipt` size validation loop, the size gate never re-runs on the final, larger receipt. The codebase's own comment on `ValidateReceiptMode::ExistingReceipt` (`verifier.rs:732-738`) explicitly acknowledges: "There is a bug which allows to create receipts that are above the size limit. Runtime has to handle them gracefully until the receipt size limit bug is fixed. See https://github.com/near/nearcore/issues/12606." Existing regression tests (`test-loop-tests/src/tests/max_receipt_size.rs::test_max_receipt_size_promise_return` and `::test_max_receipt_size_value_return`) reproduce and document this exact bypass, and the comments there state the receipt "should be rejected, but currently isn't because of a bug."

### Impact Explanation
Any unprivileged contract caller can submit a `FunctionCall` transaction against a contract that either (a) creates a promise DAG using `promise_return` so the final receipt's `output_data_receivers` are appended post-validation, or (b) returns a large value that becomes an oversized `Data` receipt. This produces a receipt whose serialized size exceeds `max_receipt_size` (4 MiB) — the same hard limit that `docs/misc/state_witness_size_limits.md` documents as bounding `ChunkStateWitness` size (~21 MiB total budget across all limits). An attacker can inflate outgoing/incoming receipt and witness sizes beyond the protocol's designed cap, undermining the resource-exhaustion protection the limit exists to enforce (`ReceiptSizeExceeded` is meant to be a hard, unconditional cap). This is a state-transition/consensus-relevant invariant violation (deterministic and reproducible by all honest nodes the same way, so it does not cause a fork, but it defeats the protocol's own size-limit invariant and inflates state-witness/receipt payload beyond its documented safety margin), reachable purely via a submitted transaction/contract call.

### Likelihood Explanation
High reachability: any account with a deployed contract using `promise_return` (a standard SDK pattern for promise chaining, NEP-defined) or returning a large value from a callback can trigger this deterministically, with no special privileges. The bug is already known and tracked (#12606) and reproduced by first-party tests, confirming it is a live, exploitable defect rather than a hypothetical.

### Recommendation
Re-validate the final receipt (including any post-hoc `output_data_receivers` mutation and the newly constructed `Data` receipts) against `max_receipt_size` at `runtime/runtime/src/lib.rs:1146-1191`, after the output-data attachment logic completes, mirroring the `validate_payload_size` remediation suggested for joserfc: apply the size check to every path that can produce or grow a receipt, not only the pre-mutation `NewReceipt` validation call site.

### Proof of Concept
Existing first-party reproduction (already in the codebase, confirming the bug): [1](#0-0) 
which builds a receipt at exactly `max_receipt_size` via `max_receipt_size_promise_return_method1`, and after `output_data_receivers` are attached post-validation, the receipt exceeds the limit yet is still processed (`assert_oversized_receipt_occurred`, confirming the on-chain receipt exceeded `max_receipt_size`). The relevant mutation-after-validation code path is: [2](#0-1) 
and the size gate that is bypassed is: [3](#0-2) 
with the acknowledged-bug comment at: [4](#0-3)

### Citations

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-208)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
#[test]
fn test_max_receipt_size_promise_return() {
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

    // User calls a contract method
    // Contract method creates a DAG with two promises: [A -then-> B]
    // When promise A is executed, it creates a third promise - `C` and does a `promise_return`.
    // The DAG changes to: [C ->then-> B]
    // The receipt for promise C is a maximum size receipt.
    // Adding the `output_data_receivers` to C's receipt makes it go over the size limit.
    let base_receipt_template = Receipt::V0(ReceiptV0 {
        predecessor_id: account.clone(),
        receiver_id: account.clone(),
        receipt_id: CryptoHash::default(),
        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: account.clone(),
            signer_public_key: account_signer.public_key().into(),
            gas_price: Balance::ZERO,
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: vec![Action::FunctionCall(Box::new(FunctionCallAction {
                method_name: "noop".into(),
                args: vec![],
                gas: Gas::ZERO,
                deposit: Balance::ZERO,
            }))],
        }),
    });
    let base_receipt_template = action_receipt_v1_to_latest(&base_receipt_template);
    let base_receipt_size = borsh::object_length(&base_receipt_template).unwrap();
    let max_receipt_size = 4_194_304;
    let args_size = max_receipt_size - base_receipt_size;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_promise_return_method1".into(),
        format!("{{\"args_size\": {}}}", args_size).into(),
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

**File:** runtime/runtime/src/lib.rs (L1146-1191)
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
            } else {
                let data = match result.result {
                    Ok(ReturnData::Value(ref data)) => Some(data.clone()),
                    Ok(_) => Some(vec![]),
                    Err(_) => None,
                };
                result.new_receipts.extend(action_receipt.output_data_receivers().iter().map(
                    |data_receiver| {
                        Receipt::V0(ReceiptV0 {
                            predecessor_id: account_id.clone(),
                            receiver_id: data_receiver.receiver_id.clone(),
                            receipt_id: CryptoHash::default(),
                            receipt: ReceiptEnum::Data(DataReceipt {
                                data_id: data_receiver.data_id,
                                data: data.clone(),
                            }),
                        })
                    },
                ));
            };
        }
```

**File:** runtime/runtime/src/verifier.rs (L681-696)
```rust
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

**File:** runtime/runtime/src/verifier.rs (L727-739)
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
```
