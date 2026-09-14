### Title
Receipt size validated before `output_data_receivers` is appended, allowing receipts to exceed `max_receipt_size` and bloat the `ChunkStateWitness` - ([File: runtime/runtime/src/verifier.rs])

### Summary
`validate_receipt` in `ValidateReceiptMode::NewReceipt` mode enforces `max_receipt_size` by measuring the borsh-serialized size of a receipt at the moment it is checked [1](#0-0) . However, a callback/promise receipt's `output_data_receivers` field can be populated *after* this validation runs (when a later promise in the DAG does a `promise_return`), growing the already-validated receipt beyond the checked size. This is the same bug class as the ModSecurity report: a component validates one representation of the data (the receipt as it stood at check time) while a different, larger representation (the receipt with `output_data_receivers` appended) is what is actually stored, sent cross-shard, and counted into the `ChunkStateWitness`. The nearcore code itself documents this as a known, currently-unfixed issue (near/nearcore#12606) and even contains workaround "pretend" clamps for it in the bandwidth-scheduler/congestion-control code.

### Finding Description
- `validate_receipt` computes `receipt_size = borsh::object_length(receipt)` and rejects the receipt if it exceeds `limit_config.max_receipt_size`, but **only** when `mode == ValidateReceiptMode::NewReceipt` [2](#0-1) .
- This check happens in `apply_action_receipt` right after a new receipt is produced by `apply_action`, via `new_result.new_receipts.iter().try_for_each(|receipt| validate_receipt(...))` [3](#0-2) .
- The contract-level `promise_return` mechanism can later attach an `output_data_receivers` entry to a *previously created* receipt in the DAG (this is how a promise chain like `A -> then -> B` gets rewired to `C -> then -> B`). That mutation happens after the receipt in question already passed the `NewReceipt` size check, so the stored/forwarded receipt's real serialized size is now larger than what was validated.
- The in-repo test `test_max_receipt_size_promise_return` explicitly demonstrates and documents this: a receipt is engineered to sit exactly at `max_receipt_size`, validation passes, then `output_data_receivers` is added, pushing the receipt above the limit — and the code comment states plainly "the receipt should be rejected, but currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)" [4](#0-3) .
- The runtime is aware oversized receipts can exist and has added defensive "pretend it's within limit" clamps elsewhere rather than fixing the root cause: `try_forward`/`ReceiptSink` clamps the receipt size to `max_receipt_size` when comparing against the bandwidth/congestion outgoing limit (`congestion_control.rs`, referenced from the spec) and `generate_bandwidth_request` does the same for `receipt_group_sizes`, both citing issue #12606 explicitly [5](#0-4) . `ValidateReceiptMode::ExistingReceipt` is also explicitly documented as intentionally more lenient than `NewReceipt` "because... there is a bug which allows to create receipts that are above the size limit. Runtime has to handle them gracefully until the receipt size limit bug is fixed" [6](#0-5) .
- `max_receipt_size` (4 MiB) is one of the hard limits designed specifically to bound the total uncompressed size of `ChunkStateWitness` (~21 MiB target) [7](#0-6) . A receipt that silently exceeds this hard limit defeats that bound.

### Impact Explanation
This is directly analogous to the ModSecurity finding: a validating component inspects/limits one form of the data, while the actual data that flows onward (stored in state, forwarded cross-shard, and embedded in the `ChunkStateWitness`) differs (is larger) from what was checked. Concretely for nearcore:
- Any account can call a contract (e.g. the standard test contract's promise/`promise_return` pattern) to construct a promise DAG whose leaf receipt is crafted to sit at exactly `max_receipt_size` before a later `promise_return` attaches an `output_data_receivers` entry, growing it past the hard limit undetected.
- Because `max_receipt_size` exists specifically to bound witness size, a single unprivileged transaction can produce a receipt that is silently larger than the protocol's stated maximum, chipping away at the 21 MiB witness budget assumption and (in aggregate, or combined with other maximized limits) can inflate `ChunkStateWitness` beyond intended bounds, risking oversized witnesses, validation cost blow-ups, or - if compounded with other size-dependent logic that assumes the `max_receipt_size` invariant holds - can produce state-transition divergence between nodes that handle the oversized receipt differently (`NewReceipt` vs. `ExistingReceipt` validation paths have different leniency, so a producer and a validator could, in principle, disagree if the "graceful handling" paths are not perfectly consistent).

### Likelihood Explanation
High likelihood of reachability: the trigger requires only a single unprivileged transaction/function call using an ordinary promise DAG with `promise_return`, which is a documented, commonly-used contract pattern, no special privileges needed. The bug is not hypothetical — it is already reproduced by an existing regression test in the repository (`test_max_receipt_size_promise_return`) and tracked as a known open issue (#12606), with the codebase working around symptoms (clamping sizes in bandwidth/congestion code) rather than closing the root cause in `apply_action_receipt`/`validate_receipt`.

### Recommendation
Re-validate (or re-check `max_receipt_size`) after `output_data_receivers` (and any other post-hoc mutation such as `promise_return` rewiring) is finalized for a receipt, before it is committed to state/forwarded, rather than only checking receipt size at the moment `apply_action` returns `new_receipts`. Alternatively, cap the total size contribution of `output_data_receivers` up front so that adding them can never push a previously-validated receipt above `max_receipt_size`, and remove the compensating "pretend the size is clamped" logic in `congestion_control.rs` once the root cause is fixed, converting the currently lenient `ExistingReceipt` validation gap into a hard invariant enforced at receipt-construction time.

### Proof of Concept
The existing in-tree test demonstrates the issue end-to-end and is the most reliable PoC:
- `test-loop-tests/src/tests/max_receipt_size.rs::test_max_receipt_size_promise_return` deploys the standard test contract, sizes a receipt to exactly `max_receipt_size`, lets it pass `NewReceipt` validation, then has a subsequent promise trigger `promise_return`, which appends `output_data_receivers` and pushes the receipt over `max_receipt_size` without being rejected, asserting `assert_oversized_receipt_occurred` at the end [8](#0-7) .

Note: I was not able to fully trace every downstream consumer that assumes `max_receipt_size` is a hard invariant (e.g. all state-witness packing logic), so the precise blast radius of "how much" a receipt can exceed the limit, and whether it can be driven arbitrarily large versus only slightly over, is not fully confirmed from the index alone; a Devin session with full repository access would be needed to trace `promise_return`/`receipt_manager.rs` exhaustively to bound the maximum overshoot.

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

**File:** runtime/runtime/src/lib.rs (L968-980)
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
                        new_result.result =
                            Err(ActionErrorKind::NewReceiptValidationError(e).into());
                    }
                }
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

**File:** docs/misc/state_witness_size_limits.md (L1-18)
```markdown
## State witness size limits

Some limits were introduced to keep the size of `ChunkStateWitness` reasonable.
`ChunkStateWitness` contains all the incoming transactions and receipts that will be processed during chunk application and in theory a single receipt could be tens of megabytes in size. Distributing a `ChunkStateWitness` this large would be troublesome, so we limit the size and number of transactions, receipts, etc. The limits aim to keep the total uncompressed size of `ChunkStateWitness` under 21MiB.

There are two types of size limits:

* Hard limit - the size must be below this limit, anything else is considered invalid
* Soft limit - things are added until the limit is exceeded, after that things stop being added. The last added thing is allowed to slightly exceed the limit.

The limits are:

* `max_transaction_size = 1.5 MiB`
  * All transactions must be below 1.5 MiB, otherwise they'll be considered invalid and rejected.
  * Previously was 4MiB, now reduced to 1.5MiB
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```
