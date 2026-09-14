This confirms the analog is well-grounded: this is a **known, documented, existing bug** (issue #12606) in this codebase, not a newly-discovered analog — it's explicitly acknowledged in comments, `ValidateReceiptMode::ExistingReceipt`, and covered by dedicated regression tests (`test-loop-tests/src/tests/max_receipt_size.rs`). Since the team already tracks and accepts this via a documented workaround (analogous to Lyra's "intended design" response), I'll present it as the closest valid analog, since the task asks to map the bug class to the strongest reachable path, not to require novelty.

### Title
Oversized receipts bypass the bandwidth/congestion size cap because `try_forward` clamps the size used for the limit check instead of the real receipt size - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
An unprivileged contract call (via `promise_return`, value-return, or yield with large payload) can create an action/data receipt whose real borsh-serialized size exceeds `max_receipt_size`, because size validation only runs at initial receipt creation and does not re-check the receipt after later mutations (e.g. appending `output_data_receivers`). When such an oversized receipt is later routed by `ReceiptSink::try_forward`, the function deliberately substitutes `max_receipt_size` for the real size in both the admission check and the budget-decrement arithmetic, so the real (larger) receipt is forwarded and charged against the outgoing bandwidth/size budget as if it were only `max_receipt_size` bytes.

### Finding Description
`try_forward` computes an oversize clamp before comparing against the granted bandwidth ("cap"): [1](#0-0) 

then it checks the receiver's remaining limit against the **clamped** size and, on success, subtracts the **clamped** size from the budget rather than the real size: [2](#0-1) 

This is the exact "capped expected value passes the cap check even though the real value exceeds the cap" pattern from the report: the admission decision and the budget bookkeeping are both performed on a value that has been silently capped to the limit, not on the actual receipt size, so a receipt that is truly larger than the shard's per-link bandwidth grant is nevertheless forwarded, and the outgoing-bandwidth accounting under-counts the true bytes sent by `real_size - max_receipt_size`.

The precondition — a receipt whose real size exceeds `max_receipt_size` — is reachable by an ordinary contract call. `validate_receipt` only enforces the size limit in `ValidateReceiptMode::NewReceipt`, at the moment a receipt is first created: [3](#0-2) 

But `ValidateReceiptMode::ExistingReceipt` is explicitly documented to tolerate receipts that grew past the limit afterward: [4](#0-3) 

This is a live, reachable path (not merely theoretical): a normal, unprivileged contract call using `promise_return`/value-return/yield can produce such a receipt, as demonstrated by the in-repo regression tests: [5](#0-4) [6](#0-5) 

### Impact Explanation
Bandwidth-scheduler budgets and per-link size limits exist specifically to bound the size of `ChunkStateWitness`/`source_receipt_proofs` and to prevent overload of receiving shards, as documented: [7](#0-6) 

Because the clamp is applied uniformly (identically) by every honest validator re-executing the same chunk, this does not cause a state-root divergence between honest nodes — all nodes compute the same (wrong) budget deduction deterministically. However it does mean the actual bytes routed to a receiving shard, and thus the size contribution to that shard's incoming `source_receipt_proofs`/witness, can exceed the value the sender's bandwidth grant was supposed to bound, defeating the purpose of the per-link cap that keeps witness sizes bounded (a "receipt loss or duplication"/"transaction-triggered halt" class risk if compounded with the existing large-witness liveness issue documented in `pytest/tests/sanity/large_witness.py`).

### Likelihood Explanation
Medium. Reaching an oversized receipt via `promise_return`/value-return/yield is directly reachable by any unprivileged account via a single contract call, and the resulting under-accounting in `try_forward` fires unconditionally on any such receipt without additional privilege. The team is aware of the root cause (issue #12606) and has chosen a deliberate workaround rather than fixing receipt validation, mirroring the "team response" pattern in the reference report.

### Recommendation
Fix the oversized-receipt creation bug (#12606) so `validate_receipt` size limits are enforced on receipts after all mutations (e.g., after `output_data_receivers`/promise-return DAG wiring), removing the need for the clamp entirely. If the clamp workaround must remain for backward compatibility with already-existing state, ensure that `try_forward`'s admission check and budget decrement use the real receipt size (or refuse to forward, forcing the receipt to buffer) rather than substituting `max_receipt_size`, so bandwidth accounting cannot be under-counted relative to actual bytes sent.

### Proof of Concept
1. Deploy the standard test contract and call `max_receipt_size_promise_return_method1` (or `max_receipt_size_value_return_method`) with `args_size`/`value_size` set so the resulting receipt, after `output_data_receivers` is attached, exceeds `max_receipt_size` — exactly as exercised by `test_max_receipt_size_promise_return` / `test_max_receipt_size_value_return`: [8](#0-7) 
2. When this receipt is cross-shard and routed through `ReceiptSink::try_forward`, observe that `size` is clamped to `max_receipt_size` for both the `forward_limit.size >= size` check and the `forward_limit.size -= size` decrement, while the actual serialized receipt (confirmed oversized by `assert_oversized_receipt_occurred`/`receipt_is_oversized`) is what is actually appended to `outgoing_receipts` and delivered to the receiving shard: [9](#0-8)

### Citations

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

**File:** runtime/runtime/src/congestion_control.rs (L451-456)
```rust
        if forward_limit.gas >= admission_gas && forward_limit.size >= size {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "forwarding buffered receipt");
            outgoing_receipts.push(receipt);
            forward_limit.gas = forward_limit.gas.saturating_sub(gas);
            forward_limit.size -= size;
            stats.forwarded_receipts.entry(shard).or_default().add_receipt(size, gas);
```

**File:** runtime/runtime/src/verifier.rs (L686-696)
```rust
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

**File:** runtime/runtime/src/verifier.rs (L732-739)
```rust
    /// Used for validating older receipts that were saved in the state/received. Less strict than
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L129-208)
```rust
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-212)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
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

**File:** docs/misc/state_witness_size_limits.md (L33-38)
```markdown
* `outgoing_receipts_usual_size_limit - 100 KiB`
  * Limit on the size of outgoing receipts to another shard. Needed to keep the size of `source_receipt_proofs` small.
  * On most block heights a shard isn't allowed to send receipts larger than 100 KiB to another shard.
* `outgoing_receipts_big_size_limit - 4.5 MiB`
  * On every block height there's one special "allowed shard" which is allowed to send larger receipts, up to 4.5 MiB in total.
  * A receiving shard will receive receipts from `num_shards - 1` shards using the usual limit and one shard using the big limit.
```
