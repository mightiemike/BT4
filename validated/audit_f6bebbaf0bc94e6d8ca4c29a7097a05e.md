### Title
Oversized receipts created via `output_data_receivers` mutation bypass the `max_receipt_size` limit before congestion/bandwidth forwarding validation - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
A single unprivileged transaction that builds a promise DAG (e.g. `promise_then`/`promise_return`) can produce an action receipt whose size is validated against `max_receipt_size` at creation time, but which is then mutated afterward — an `output_data_receivers` entry (a `DataReceiver`) is appended to wire it into the promise chain — growing the receipt beyond the declared/configured size limit *after* the size check already passed. This is structurally the same bug class as the Deskflow report: data keeps being appended to an object past its declared/checked size before the code path that is supposed to enforce the limit runs, and downstream code has to work around the now-oversized object rather than reject it.

### Finding Description
`validate_receipt`/`ReceiptValidationError::ReceiptSizeExceeded` in `runtime/runtime/src/verifier.rs` checks a receipt's `max_receipt_size` at the moment a `FunctionCall`/promise action creates it. However, in a promise DAG such as `[A -then-> B]`, when promise `A` executes and calls `promise_return`, the runtime rewires the DAG by creating promise `C` and appending an `output_data_receivers` entry to `C`'s already-created action receipt (`core/primitives/src/receipt.rs` `ActionReceipt.output_data_receivers`, populated via `receipt_manager.rs`). This append happens **after** the receipt already passed its `max_receipt_size` check, so the final receipt handed to congestion control can legitimately exceed the configured limit.

The `runtime/runtime/src/congestion_control.rs` code is explicitly aware of this and works around it rather than fixing the root cause: [1](#0-0) 
which clamps the observed size down to `max_receipt_size` "to avoid receipts getting stuck," referencing `https://github.com/near/nearcore/issues/12606`, and again in `generate_bandwidth_request`: [2](#0-1) 

The actual reachable path from an unprivileged caller and the acknowledged nature of the bug is directly demonstrated in the test suite: [3](#0-2) [4](#0-3) 

The comment on lines 124-128 states plainly: "Size of this receipt will be equal to `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently isn't because of a bug."

### Impact Explanation
This is a size-limit bypass reachable purely from a signed transaction/RPC call by any account — no validator, peer, or operator privilege required (matching the Deskflow class: "receiver accumulates data beyond its configured size limit before final validation"). Concretely:
- It allows a single transaction to produce and persist/forward a receipt whose true serialized size exceeds `max_receipt_size`, undermining the invariant used throughout `ReceiptSink`/`OutgoingMetadatas`/bandwidth-scheduler math (`runtime/runtime/src/congestion_control.rs`), which assumes receipts are bounded to `max_receipt_size` for congestion-gas/size accounting and for building `ChunkStateWitness` (`docs/misc/state_witness_size_limits.md`, whose 21 MiB total bound assumes `max_receipt_size = 4 MiB` per receipt).
- Because forwarding/buffering code silently clamps the *accounted* size to `max_receipt_size` rather than rejecting the receipt, the discrepancy between accounted size and actual stored/transmitted size can inflate witness size, storage, and receipt-buffer memory beyond the limits the protocol relies on for state-witness size guarantees and congestion bookkeeping — the same "receiver accumulates data beyond the configured limit" outcome as the Deskflow clipboard bug, just in the receipt/congestion-control pipeline instead of a clipboard buffer.
- The nearcore team's own workaround/comments treat this as a known-but-unfixed correctness gap in the size-limit enforcement, not a benign quirk.

### Likelihood Explanation
High likelihood of triggerability: this requires only a standard `FunctionCall` action from any account building a promise DAG that uses `promise_then`/`promise_return` to hit close to `max_receipt_size`, exactly as reproduced by the existing regression test `test_max_receipt_size_promise_return`. No special permissions, staking, or validator role is needed — it is a pure transaction/contract-call path.

### Recommendation
Re-validate (or reject) a receipt against `max_receipt_size` after all post-creation mutations (notably `output_data_receivers` appends performed during `promise_return`/DAG rewiring in `receipt_manager.rs`) rather than only at initial creation time, before the receipt is handed to `ReceiptSink::forward_or_buffer_receipt`. Remove the compensating "pretend receipts are at most `max_receipt_size`" clamps in `congestion_control.rs` (`try_forward`, `generate_bandwidth_request`) once the root-cause validation is fixed, since they currently mask oversized receipts (silently under-accounting congestion gas/bandwidth cost) instead of preventing them.

### Proof of Concept
Use the existing repro directly, which is a single unprivileged deployer/caller flow: [5](#0-4) 
1. Deploy `near_test_contracts::rs_contract()` from any funded account.
2. Call `max_receipt_size_promise_return_method1` with `args_size` sized so the intermediate promise `C`'s receipt lands exactly at `max_receipt_size` (4 MiB) before `promise_return` rewires the DAG `[A -then-> B]` into `[C -then-> B]`.
3. The `output_data_receivers` append onto `C`'s receipt (post size-check) pushes it over `max_receipt_size`; `assert_oversized_receipt_occurred` in the test confirms the runtime observes and processes (rather than rejects) the oversized receipt.

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

**File:** runtime/runtime/src/congestion_control.rs (L556-562)
```rust
        // There's a bug which allows to create receipts above `max_receipt_size` (https://github.com/near/nearcore/issues/12606).
        // This could cause problems with bandwidth scheduler which would generate requests for size above max size, and these
        // requests would never be fulfilled. For bandwidth requests let's pretend that all sizes are below `max_receipt_size`.
        // The same pretending logic is also present in `try_forward` which compares receipt size with outgoing limit.
        // This logic should also make it possible to do protocol upgrades that lower `max_receipt_size` without too much trouble.
        let sizes_iter = receipt_sizes_iter
            .map_ok(|group_size| std::cmp::min(group_size, params.max_receipt_size));
```

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
