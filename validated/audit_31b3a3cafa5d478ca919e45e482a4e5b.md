### Title
Unbounded receipt-size growth after size validation permits acceptance of oversized receipts (analog to CVE-2019-12526 buffer overflow via unchecked response size) - (File: `runtime/runtime/src/verifier.rs`)

### Summary
Squid's CVE-2019-12526 stems from failing to verify that attacker-influenced response data fits inside an allocated buffer before copying it, causing heap overflow. The nearcore analog is `validate_receipt`/`validate_action_receipt` in `runtime/runtime/src/verifier.rs`, which measures a receipt's serialized size against `max_receipt_size` *before* the receipt is finalized, but downstream code (`output_data_receivers` injection in `receipt_manager.rs::create_action_receipt`, and value-return handling that builds a `Data` receipt from an arbitrary-length WASM return value) can grow the receipt past that already-validated size. The size check is therefore not a binding invariant on the final serialized receipt — analogous to Squid checking length at the wrong point and later overflowing the buffer.

### Finding Description
`validate_receipt` (`runtime/runtime/src/verifier.rs:681-696`) computes `borsh::object_length(receipt)` and rejects if `receipt_size > limit_config.max_receipt_size`, but only in `ValidateReceiptMode::NewReceipt`. The comment on `ValidateReceiptMode::ExistingReceipt` (`verifier.rs:727-739`) explicitly documents: *"There is a bug which allows to create receipts that are above the size limit. Runtime has to handle them gracefully until the receipt size limit bug is fixed. See https://github.com/near/nearcore/issues/12606."*

The root cause: a contract can create a receipt whose measured size is exactly at (or under) `max_receipt_size` and passes validation, but the runtime subsequently mutates the receipt — e.g. `ReceiptManager::create_action_receipt` (`receipt_manager.rs:112-138`) pushes an additional `DataReceiver` into `output_data_receivers` of an *already validated* receipt when a dependent promise is created (`create_action_receipt`, lines 119-125), and `value_return` can produce a `Data` receipt whose payload equals the returned buffer size, which is then wrapped in a receipt that exceeds `max_receipt_size` after wrapping overhead. This is confirmed by three test cases explicitly documented as demonstrating the un-fixed bug:
- `test_max_receipt_size_promise_return` (`test-loop-tests/src/tests/max_receipt_size.rs:124-208`): "Size of this receipt will be equal to `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and the receipt's size will go above `max_receipt_size`. The receipt should be rejected, but currently isn't."
- `test_max_receipt_size_value_return` (`max_receipt_size.rs:210-267`): a returned value wrapped in a `Data` receipt exceeds `max_receipt_size` and is not rejected.
- `test_max_receipt_size_yield_resume` shows the yield/resume path correctly enforces the limit, contrasting with the two broken paths above.

The runtime is aware of the resulting oversized receipts reaching the cross-shard forwarding path and has added a defensive clamp rather than a fix: `ReceiptSinkV2::try_forward` (`runtime/runtime/src/congestion_control.rs:403-427`) explicitly clamps `size` to `max_receipt_size` "to avoid receipts getting stuck," citing the same issue #12606. This clamp is a size-accounting workaround, not a correctness fix — the actual oversized receipt (with its real, larger serialized size) is still what gets included in the chunk and its state witness, and is still what bandwidth/congestion accounting size limits (`outgoing_receipts_usual_size_limit`, `outgoing_receipts_big_size_limit`, `max_congestion_memory_consumption`) are supposed to bound but no longer do accurately, since the code pretends the receipt is `max_receipt_size` even when it is not.

### Impact Explanation
This breaks a hard invariant the protocol relies on for keeping `ChunkStateWitness` size bounded (`docs/misc/state_witness_size_limits.md:16-18`): "All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected." An unprivileged contract caller (via `promise_then`/`promise_batch_then` creating a dependent receipt, or via returning a large value from a callback) can produce receipts that exceed `max_receipt_size` while passing `NewReceipt` validation, because the size check happens before `output_data_receivers`/value-wrapping mutation. Because `outgoing_receipts_usual_size_limit`/`outgoing_receipts_big_size_limit` and the witness-size budget math (`docs/misc/state_witness_size_limits.md:33-40`) assume no receipt exceeds `max_receipt_size`, an attacker can inflate actual witness/data size beyond the ~21 MiB design budget, which can cause disproportionate resource consumption for chunk validators reconstructing/re-executing witnesses, and — if the clamp-based congestion accounting diverges from actual bytes forwarded across different node code-paths/versions — creates a state-root or accounting-divergence risk between honest nodes that handle the "existing oversized receipt" tolerance differently.

### Likelihood Explanation
High feasibility: the bug is not speculative — it is reproduced by first-party tests (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`) that assert the buggy (non-rejecting) behavior currently occurs, and the runtime has permanent code (`ValidateReceiptMode::ExistingReceipt`, the `try_forward` size clamp) built specifically to "handle them gracefully" rather than reject them. Any account with a deployed contract can trigger this via a single transaction chain (`promise_create`/`promise_then`/`value_return`), requiring no privileged role, staking, or validator collusion.

### Recommendation
Enforce `max_receipt_size` validation on the fully-finalized receipt — after `output_data_receivers` are appended and after the final wrapping of returned values into `Data`/`PromiseResume` receipts — rather than only at initial creation time. Concretely: re-validate (or cap) receipt size in `ReceiptManager::create_action_receipt` when pushing to `output_data_receivers`, and re-validate in the value-return/data-receipt construction path in `runtime/runtime/src/lib.rs` before the receipt is committed to `outgoing_receipts`/the trie. Once this is fixed, the `try_forward` clamp workaround in `congestion_control.rs:413-427` and the `ExistingReceipt` bug-tolerance mode in `verifier.rs` should be revisited/removed to restore the hard invariant.

### Proof of Concept
1. Deploy `near_test_contracts::rs_contract()`.
2. Call `max_receipt_size_promise_return_method1` with `args_size` computed so the initial receipt's borsh size equals `max_receipt_size` (passes `NewReceipt` validation).
3. The runtime subsequently appends `output_data_receivers` for the dependent promise (`ReceiptManager::create_action_receipt`), pushing the receipt above `max_receipt_size` without re-validation.
4. Observe (as in `test_max_receipt_size_promise_return`, `test-loop-tests/src/tests/max_receipt_size.rs:130-208`) that the oversized receipt is accepted and forwarded/included rather than rejected with `ReceiptSizeExceeded`.
5. Equivalently, call `max_receipt_size_value_return_method` with `value_size = max_receipt_size` (`max_receipt_size.rs:216-267`) to produce an oversized `Data` receipt via `value_return`, which is likewise accepted instead of rejected. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** runtime/runtime/src/receipt_manager.rs (L112-138)
```rust
    pub(super) fn create_action_receipt(
        &mut self,
        input_data_ids: Vec<CryptoHash>,
        receipt_indices: Vec<ReceiptIndex>,
        receiver_id: AccountId,
    ) -> Result<ReceiptIndex, VMLogicError> {
        assert_eq!(input_data_ids.len(), receipt_indices.len());
        for (data_id, receipt_index) in input_data_ids.iter().zip(receipt_indices.into_iter()) {
            self.action_receipts
                .get_mut(receipt_index as usize)
                .ok_or(HostError::InvalidReceiptIndex { receipt_index })?
                .output_data_receivers
                .push(DataReceiver { data_id: *data_id, receiver_id: receiver_id.clone() });
        }

        let new_receipt = ActionReceiptMetadata {
            receiver_id,
            refund_to: None,
            output_data_receivers: vec![],
            input_data_ids,
            actions: vec![],
            is_promise_yield: false,
        };
        let new_receipt_index = self.action_receipts.len() as ReceiptIndex;
        self.action_receipts.push(new_receipt);
        Ok(new_receipt_index)
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

**File:** docs/misc/state_witness_size_limits.md (L13-40)
```markdown
* `max_transaction_size = 1.5 MiB`
  * All transactions must be below 1.5 MiB, otherwise they'll be considered invalid and rejected.
  * Previously was 4MiB, now reduced to 1.5MiB
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
* `max_receipt_total_input_size - 4 MiB + 640 B`
  * Hard limit on the combined size of a receipt's resolved promise inputs (the `ReceivedData` referenced by its `input_data_ids`). Receipts which exceed it fail with `TotalPromiseInputSizeExceeded` without executing their actions.
  * These inputs are read before `per_receipt_storage_proof_size_limit` starts counting, so without this limit a single receipt could pull `max_number_input_data_dependencies * max_receipt_size` (128 * 4 MiB) into the witness.
  * The limit is `max_length_returned_data` (4 MiB) plus the worst-case per-input framing overhead (128 * 5 bytes), so 4 MiB of input data always fits no matter how it's split across data receipts.
* `combined_transactions_size_limit - 4 MiB`
  * Hard limit on total size of transactions from this and previous chunk. `ChunkStateWitness` contains transactions from two chunks, this limit applies to the sum of their sizes.
* `new_transactions_validation_state_size_soft_limit - 500 KiB`
  * Validating new transactions generates storage proof (recorded trie nodes), which has to be limited. Once transaction validation generates more storage proof than this limit, the chunk producer stops adding new transactions to the chunk.
* `per_receipt_storage_proof_size_limit - 4 MB`
  * Executing a receipt generates storage proof. A single receipt is allowed to generate at most 4MB of storage proof. This is a hard limit, receipts which generate more than that will fail.
* `main_storage_proof_size_soft_limit - 4 MB`
  * This is a limit on the total size of storage proof generated by receipts in one chunk. Once receipts generate more storage proof than this limit, the chunk producer stops processing receipts and moves the rest to the delayed queue.
  * It's a soft limit, which means that the total size of storage proof could reach 8 MB (3.99MB + one receipt which generates 4MB of storage proof)
  * Due to implementation details it's hard to find the exact amount of storage proof generated by a receipt, so an upper bound estimation is used instead. This upper bound assumes that every removal generates additional 2000 bytes of storage proof, so receipts which perform a lot of trie removals might be limited more than theoretically applicable.
* `outgoing_receipts_usual_size_limit - 100 KiB`
  * Limit on the size of outgoing receipts to another shard. Needed to keep the size of `source_receipt_proofs` small.
  * On most block heights a shard isn't allowed to send receipts larger than 100 KiB to another shard.
* `outgoing_receipts_big_size_limit - 4.5 MiB`
  * On every block height there's one special "allowed shard" which is allowed to send larger receipts, up to 4.5 MiB in total.
  * A receiving shard will receive receipts from `num_shards - 1` shards using the usual limit and one shard using the big limit.

In total that gives 4 MiB + 500 KiB + 8MB + 4 MiB + 5*100 KiB + 4.5 MiB ~= 21 MiB of maximum witness size. Possibly a little more on missing chunks.
```
