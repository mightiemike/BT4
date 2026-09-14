### Title
Oversized-receipt bypass of `max_receipt_size` via post-validation `output_data_receivers` mutation — (File: `runtime/runtime/src/lib.rs`)

### Summary
A single unprivileged contract call can produce an outgoing cross-shard receipt whose serialized size exceeds the protocol's `max_receipt_size` limit, because the size/validity check happens *before* `output_data_receivers` are appended to the receipt returned via `ReturnData::ReceiptIndex`, not after. This mirrors the TensorFlow `Dilation2DBackpropInput` root cause: a bound is validated against one state of the data (the receipt as constructed, guaranteed ≤ limit), but the value is subsequently mutated by a different code path (data-receiver propagation) without re-checking that bound before it is used elsewhere (bandwidth-scheduler/congestion-control accounting, cross-shard serialization).

### Finding Description
When a `FunctionCall` action executes and calls `promise_return` on a newly-created promise, the callback semantics require the runtime to graft the caller's `output_data_receivers` onto the *new* receipt referenced by `ReturnData::ReceiptIndex(receipt_index)`: [1](#0-0) 

This happens in `apply_action_receipt`, **after** the receipt has already been constructed and validated as being within size limits, and the code path unconditionally does `new_action_receipt.output_data_receivers.extend_from_slice(...)` with no subsequent check that the mutated receipt is still `<= max_receipt_size`. The receipt is then placed on `new_receipts` and forwarded via `ReceiptSink::forward_or_buffer_receipt` for cross-shard delivery.

The congestion-control forwarding path is explicitly documented as containing a size-limit clamp *workaround* for exactly this class of oversized receipt (issue #12606), rather than a fix: [2](#0-1) 

The repository has a dedicated regression test acknowledging this is a known, currently-unfixed bug: [3](#0-2) 

The test constructs the scenario purely via normal `SignedTransaction::deploy_contract` / `SignedTransaction::call` from an ordinary unprivileged account — no validator, malicious-peer, or protocol-privileged access is required: [4](#0-3) 

### Impact Explanation
`max_receipt_size` is a protocol invariant relied upon by the bandwidth scheduler (which grants byte budgets per shard-link assuming individual receipts never exceed this bound, see `runtime/runtime/src/congestion_control.rs` clamp workaround) and by cross-shard serialization/proof-size accounting used in stateless validation. A receipt that silently exceeds `max_receipt_size` can:
- desynchronize the deterministic size accounting used by congestion control / bandwidth scheduler grants (since the clamp is a stop-gap "bug workaround," not a guarantee of correctness across all code paths that assume the invariant holds),
- potentially cause a chunk state witness / storage-proof size accounting mismatch between the producer (which built the state) and validators (which re-execute against recorded partial storage) if any consuming code path assumes `receipt.len() <= max_receipt_size`,
- in the worst case, a runtime panic or receipt handling divergence between differently-configured or differently-versioned honest nodes, which is a state-transition-integrity concern.

This is a High-severity class of bug (a boundary invariant is not enforced end-to-end, exactly like the TF CVE, where a per-dimension bound was checked but not the composite index actually used for the write) even though the currently observed manifestation ("test currently isn't rejected, but runtime shouldn't die") is already tracked by the nearcore team as issue #12606.

### Likelihood Explanation
Reachable with a single deployed contract and two ordinary transactions from an unprivileged account (`deploy_contract` + `call`), no special permissions, no validator or network role needed. This is a trivial, deterministic trigger.

### Recommendation
Re-validate (or re-clamp, deterministically and identically on all nodes) receipt size **after** `output_data_receivers` are appended to a `ReturnData::ReceiptIndex`-referenced receipt in `apply_action_receipt` (`runtime/runtime/src/lib.rs:1152-1170`), before the receipt is handed to `ReceiptSink::forward_or_buffer_receipt`. Reject or truncate consistently (matching the protocol-committed behavior) instead of relying on the size-clamp workaround deep in `congestion_control.rs`, and add a state-transition validity check so an oversized receipt cannot be silently accepted by chunk-producing nodes while causing size-limit desync elsewhere.

### Proof of Concept
The existing repository test is a self-contained, unprivileged PoC: [5](#0-4) 
It deploys a contract with a normal user account, calls a method that creates a promise DAG `[A -> B]` where `A` creates promise `C` and calls `promise_return`, sizing `C`'s receipt to exactly `max_receipt_size` at construction time; the subsequent `output_data_receivers` graft pushes it over the limit, which the comment at line 124-128 states is not currently rejected due to the referenced bug.

### Citations

**File:** runtime/runtime/src/lib.rs (L1152-1170)
```rust
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

**File:** protocol-model/spec/cross-shard-congestion.md (L160-177)
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-207)
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
```
