I have enough detail to write the final analog analysis. This is a well-documented, acknowledged bug (nearcore issue #12606) that is precisely analogous to the axios `maxContentLength` bug: a size limit is checked, logged, but explicitly *not enforced* — the oversized item is allowed to pass through the pipeline anyway with the code merely "pretending" it's within limits.

### Title
Unenforced `max_receipt_size` limit allows oversized receipts to bypass congestion/bandwidth admission, enabling a transaction-triggered resource-exhaustion / state-witness-size DoS - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`validate_receipt` in `runtime/runtime/src/verifier.rs` only rejects a newly-created receipt for exceeding `max_receipt_size` when validated in `ValidateReceiptMode::NewReceipt` mode. [1](#0-0)  However, a receipt can be produced by a contract in a way that it is exactly at the limit at creation time, then mutated afterward (e.g. `output_data_receivers` appended during `promise_return`/callback wiring) so its *final* borsh-serialized size exceeds `max_receipt_size` without ever being re-validated — this is the acknowledged bug tracked as nearcore issue #12606. [2](#0-1)  Rather than fixing validation to catch this, the runtime's cross-shard forwarding path explicitly detects the oversize condition, logs a debug message, and then "pretends" the receipt fits by clamping its accounted size down to `max_receipt_size` for all admission/limit checks — exactly mirroring the axios `maxContentLength` flaw of detecting-but-not-enforcing a size bound. [3](#0-2) 

### Finding Description
The relevant code path is `ReceiptSinkV2::try_forward`:
```
// There is a bug which allows to create receipts that are above the size limit. Receipts
// above the size limit might not fit under the maximum outgoing size limit. Let's pretend
// that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
// See https://github.com/near/nearcore/issues/12606
let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
if size > max_receipt_size {
    tracing::debug!(...);
    size = max_receipt_size;
}
``` [3](#0-2) 

This clamp is applied both when deciding whether to forward the receipt to the outgoing-receipts vector for a chunk (`forward_limit.size >= size`, `:451`) and when generating bandwidth-scheduler requests for the next height (`generate_bandwidth_request`, which also clamps group sizes down to `max_receipt_size` for the same reason). [4](#0-3)  The design doc for this component confirms the workaround is deliberate and applies at two separate call sites. [5](#0-4) 

The consequence: a receipt whose *actual* wire size exceeds `max_receipt_size` (documented ceiling used to bound `ChunkStateWitness` size to ~21 MiB, per `docs/misc/state_witness_size_limits.md`) is charged only `max_receipt_size` bytes against the bandwidth grant and the outgoing size budget. [6](#0-5)  An attacker who can trigger such a receipt (e.g. via `promise_return`/callback `output_data_receivers` growth as reproduced in `test_max_receipt_size_promise_return`, or via a large returned value as in `test_max_receipt_size_value_return`) can therefore cause a receipt to be forwarded/counted as if it were `max_receipt_size` while it actually consumes more bytes of the real bandwidth/size budget and, transitively, more bytes of the witness than the accounting model assumes. [7](#0-6) [8](#0-7) 

`ValidateReceiptMode::ExistingReceipt` explicitly documents that this leniency is intentional "until the receipt size limit bug is fixed," meaning oversized receipts are tolerated indefinitely throughout the receipt lifecycle (delayed queue, incoming, forwarding) rather than being rejected once detected. [9](#0-8) 

### Impact Explanation
Because the size accounting used for congestion/bandwidth admission is falsified (clamped down) instead of enforced, an attacker-triggered oversized receipt can be forwarded/buffered/counted as consuming only `max_receipt_size` bytes while its true payload is larger. This breaks the invariant that the sum of witness-relevant sizes (outgoing receipts, congestion-info `receipt_bytes`, bandwidth requests) stays within the documented ~21 MiB bound designed to keep `ChunkStateWitness` transportable and validatable. [10](#0-9)  Repeated exploitation could inflate real witness/receipt payload sizes beyond the limits the system was designed around, straining chunk producers/validators that must distribute and validate the witness — a resource-exhaustion condition directly analogous to the axios DoS (a documented size check whose violation is silently tolerated rather than enforced, defeating the purpose of the bound).

### Likelihood Explanation
The bug is triggerable by any unprivileged account issuing ordinary transactions/contract calls: the existing test suite in `test-loop-tests/src/tests/max_receipt_size.rs` demonstrates two independent, non-privileged ways to construct a receipt whose real size passes initial `NewReceipt` validation but ends up oversized afterward (`promise_return` with `output_data_receivers` growth, and `value_return` with a maximal-size value wrapped into a `DataReceipt`), and confirms via `assert_oversized_receipt_occurred` that such oversized receipts do appear as incoming receipts on-chain. [11](#0-10)  This is not a hypothetical: it is a known, reproduced, currently-unfixed bug in the shipped code with an open tracking issue (#12606) and an explicit "workaround" rather than a fix.

### Recommendation
Re-validate the finalized receipt (after all mutations such as `output_data_receivers` are applied) against `max_receipt_size` before it is admitted to the outgoing/delayed pipeline, and reject/fail the originating action instead of silently clamping the accounted size in `try_forward`/`generate_bandwidth_request`. If backward compatibility with already-produced legacy oversized receipts is required, size accounting should use the *real* size for admission decisions while only using a legacy/tolerant path for already-persisted historical receipts, rather than permanently pretending all receipts fit.

### Proof of Concept
The existing repository test `test_max_receipt_size_promise_return` is a working PoC: it deploys a contract, computes `args_size` so the initial receipt is exactly `max_receipt_size`, calls `max_receipt_size_promise_return_method1` to create a DAG `[A -then-> B]` where executing `A` performs `promise_return` of a new promise `C`, which mutates `output_data_receivers` and pushes the receipt over the limit; `assert_oversized_receipt_occurred` then confirms an oversized receipt shows up as an incoming receipt on-chain despite the limit. [12](#0-11)  The size-clamping code that allows this oversized receipt to still be forwarded/counted under the size limit is at `runtime/runtime/src/congestion_control.rs:403-427` (`ReceiptSinkV2::try_forward`). [13](#0-12)

### Citations

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

**File:** runtime/runtime/src/verifier.rs (L727-740)
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-267)
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

/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
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

**File:** protocol-model/spec/cross-shard-congestion.md (L164-165)
```markdown
1. If `size > max_receipt_size`, size is clamped to `max_receipt_size` for the limit
   comparison (bug workaround for oversized receipts, issue #12606, `:417`).
```

**File:** docs/misc/state_witness_size_limits.md (L3-4)
```markdown
Some limits were introduced to keep the size of `ChunkStateWitness` reasonable.
`ChunkStateWitness` contains all the incoming transactions and receipts that will be processed during chunk application and in theory a single receipt could be tens of megabytes in size. Distributing a `ChunkStateWitness` this large would be troublesome, so we limit the size and number of transactions, receipts, etc. The limits aim to keep the total uncompressed size of `ChunkStateWitness` under 21MiB.
```

**File:** docs/misc/state_witness_size_limits.md (L16-18)
```markdown
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```
