I have enough evidence now to write the final answer. The bug (GitHub issue #12606) is well-documented, explicitly acknowledged in code comments and tests, and is reachable by any unprivileged contract caller: `create_action_receipt` in `receipt_manager.rs` appends `DataReceiver` entries to `output_data_receivers` *after* the size-bounded actions were already validated, so a receipt that was exactly at `max_receipt_size` when checked can grow past the limit once its output/return-value data is attached — the runtime already has a "grudging" workaround (clamping) rather than a true fix, and the impact is a receipt that violates the protocol's own committed size invariant.

### Title
Receipt size limit bypass via post-validation `output_data_receivers`/return-value growth allows oversized receipts to enter cross-shard forwarding and congestion accounting - (File: `runtime/runtime/src/receipt_manager.rs`, `runtime/runtime/src/verifier.rs`, `runtime/runtime/src/congestion_control.rs`)

### Summary
`validate_receipt`/`validate_action_receipt` in `runtime/runtime/src/verifier.rs` enforce `max_receipt_size` only in `ValidateReceiptMode::NewReceipt`, but a contract invoked by any ordinary transaction can grow a receipt past that limit *after* it was constructed and implicitly "sized," because `output_data_receivers` (via `promise_then`/`promise_return`-style host calls) and returned values are appended by `ReceiptManager::create_action_receipt` [1](#0-0)  without re-checking the resulting receipt's total borsh size against `max_receipt_size`. This is the exact class of bug the external report describes: an untrusted party (a smart contract, reachable from any RPC caller's transaction) can produce a data structure whose size is not actually bounded by the check that is supposed to bound it, and that oversized structure is then propagated to code paths (`ReceiptSinkV2::try_forward`, congestion accounting, state witness inclusion) that assume the limit holds.

### Finding Description
`validate_receipt` computes `borsh::object_length(receipt)` and compares it to `limit_config.max_receipt_size` [2](#0-1) . This check only runs once, at receipt-validation time. However, a contract can build a promise chain where an intermediate receipt is sized right up to `max_receipt_size` and then have `output_data_receivers` appended to it afterward (when a subsequent `.then()` promise is created), or have a large return value wrapped into a `Data` receipt whose size was never checked against `max_receipt_size` at all for the "return value" path. The codebase's own tests document this directly: `test_max_receipt_size_promise_return` and `test_max_receipt_size_value_return` in `test-loop-tests/src/tests/max_receipt_size.rs` construct exactly this scenario and assert that an oversized receipt is produced and accepted, referencing the tracked bug [3](#0-2) [4](#0-3) .

The runtime is aware of this and has added a defensive workaround rather than a fix: `ValidateReceiptMode::ExistingReceipt` explicitly documents that it "has to handle them gracefully until the receipt size limit bug is fixed" [5](#0-4) , and `ReceiptSinkV2::try_forward` clamps any receipt whose size exceeds `max_receipt_size` down to `max_receipt_size` purely "to avoid receipts getting stuck," citing the same GitHub issue #12606 [6](#0-5) .

This maps directly onto the ScribeOptimistic pattern: an untrusted party (the contract/tx signer, analogous to the malicious feed) creates a data object whose size validation is bypassed, and that data is then propagated through machinery (`try_forward`, congestion/bandwidth accounting, state witness construction) that was designed assuming the size invariant holds — precisely the "unbounded array reaching downstream processing that assumed a bound" bug class from the report.

### Impact Explanation
An oversized receipt breaks the size invariant that `max_receipt_size` is supposed to guarantee for `ChunkStateWitness` construction (`docs/misc/state_witness_size_limits.md` states the whole witness-size budget of ~21 MiB is derived assuming every receipt individually respects `max_receipt_size`) [7](#0-6) . Because the clamp in `try_forward` only affects the *bandwidth/congestion accounting* size used for admission decisions, not the actual bytes that must be encoded into `outgoing_receipts`/state witnesses, a contract that repeatedly triggers this pattern can inflate real receipt/witness payload beyond the sizes the protocol's cost and gas-limit model assumes, undermining the guarantees other size-based invariants (congestion control admission, witness size caps) depend on for consistent, resource-bounded chunk production and validation across all nodes.

### Likelihood Explanation
This is reachable by any unprivileged account issuing an ordinary `FunctionCall` transaction against a contract that uses common promise-chaining host functions (`promise_then`, `promise_return`, or simply returning a large value) — no special privileges, validator role, or network position are required. The nearcore team itself has already acknowledged and reproduced this exact behavior with dedicated regression tests (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`), confirming it is concretely triggerable today rather than purely theoretical.

### Recommendation
Re-validate the final, fully-assembled receipt's size (including all appended `output_data_receivers` and materialized return-value `Data` receipts) against `max_receipt_size` at the point where the receipt is finalized and about to leave the VM/runtime boundary, rather than only checking size at an earlier construction step. Alternatively, bound the cumulative size contribution of `output_data_receivers` and returned values incrementally (similar to how `max_state_init_entries` bounds cumulative entries across actions in `runtime/runtime/src/action_validation.rs`) so no combination of host-function calls can push a receipt's serialized size past the configured limit after the fact.

### Proof of Concept
The existing regression tests already constitute a proof of concept for this exact path:
- `test_max_receipt_size_promise_return` builds a promise DAG `A -then-> B` where `A` creates promise `C`, does `promise_return`, and the `output_data_receivers` appended to `C`'s receipt push it over `max_receipt_size` [8](#0-7) .
- `test_max_receipt_size_value_return` has a contract return a value of size `max_receipt_size`, which is wrapped in a `Data` receipt that is itself now larger than `max_receipt_size` [9](#0-8) . Both tests call `assert_oversized_receipt_occurred`, which scans the chain for a receipt whose size exceeds `max_receipt_size` and asserts one was found [10](#0-9) , confirming the bypass is exploitable purely via a standard signed transaction to a deployed contract.

### Citations

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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-155)
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
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-217)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
#[test]
fn test_max_receipt_size_value_return() {
    init_test_logger();
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L350-416)
```rust
/// Assert that there was an incoming receipt with size above max_receipt_size
fn assert_oversized_receipt_occurred(node: &TestLoopNode<'_>) {
    let client = node.client();
    let chain = &client.chain;
    let epoch_manager = &*client.epoch_manager;

    let tip = chain.head().unwrap();
    let epoch_id = epoch_manager.get_epoch_id(&tip.last_block_hash).unwrap();
    let protocol_version = epoch_manager.get_epoch_protocol_version(&epoch_id).unwrap();
    let runtime_config = client.runtime_adapter.get_runtime_config(protocol_version);
    let max_receipt_size = runtime_config.wasm_config.limit_config.max_receipt_size;

    let mut block = chain.get_block(&tip.last_block_hash).unwrap();

    // Go over all blocks down to genesis looking for a receipt above max_receipt_size.
    loop {
        if block.header().is_genesis() {
            panic!("Didn't find receipt with size above max_receipt_size!");
        }
        let prev_block = chain.get_block(block.header().prev_hash()).unwrap();

        let shard_layout = epoch_manager
            .get_shard_layout(&epoch_manager.get_epoch_id(block.hash()).unwrap())
            .unwrap();

        let oversized = if ProtocolFeature::Spice.enabled(protocol_version) {
            // With spice chunks are executed asynchronously and their produced receipts are
            // persisted as receipt proofs keyed by the block in which the chunk was applied,
            // rather than as incoming receipts on the following block.
            shard_layout.shard_ids().any(|shard_id| {
                chain
                    .chain_store()
                    .iter_receipt_proofs_for_shard(block.hash(), shard_id)
                    .iter()
                    .flat_map(|proof| proof.0.iter())
                    .any(|receipt| receipt_is_oversized(receipt, max_receipt_size))
            })
        } else {
            block.chunks().iter_new().any(|new_chunk| {
                let shard_id = new_chunk.shard_id();
                let prev_shard_index = epoch_manager
                    .get_prev_shard_id_from_prev_hash(block.header().prev_hash(), shard_id)
                    .unwrap()
                    .2;
                let prev_height_included =
                    prev_block.chunks().get(prev_shard_index).unwrap().height_included();
                let incoming_receipts_proofs = get_incoming_receipts_for_shard(
                    &chain.chain_store,
                    epoch_manager,
                    shard_id,
                    &shard_layout,
                    *block.hash(),
                    prev_height_included,
                    ReceiptFilter::TargetShard,
                )
                .unwrap();
                incoming_receipts_proofs
                    .iter()
                    .flat_map(|response| response.1.iter())
                    .flat_map(|proof| proof.0.iter())
                    .any(|receipt| receipt_is_oversized(receipt, max_receipt_size))
            })
        };

        if oversized {
            return;
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

**File:** docs/misc/state_witness_size_limits.md (L16-18)
```markdown
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```
