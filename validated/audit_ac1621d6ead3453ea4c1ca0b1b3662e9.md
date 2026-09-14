### Title
Receipts generated via `promise_return`/`value_return` can exceed `max_receipt_size` and are still accepted into the state transition - ([File: runtime/runtime/src/verifier.rs])

### Summary
A single unprivileged function-call transaction can cause the runtime to produce a receipt (an `ActionReceipt` via `promise_return`, or a `DataReceipt` via `value_return`) whose *final* borsh-encoded size exceeds the protocol's `max_receipt_size` limit, because the size check in `validate_receipt` is performed at a point where `output_data_receivers` has not yet been appended to the receipt, while the appending happens afterward in `runtime/runtime/src/lib.rs`. This is functionally analogous to the ERC-820/ERC-1820 bug class described in the report: a validation routine checks/derives its result using a stale or incomplete size/parameter, so a value that should be rejected (oversized receipt / malformed staticcall response) is instead silently accepted, producing divergent or unsafe downstream behavior.

### Finding Description
`validate_receipt` in `runtime/runtime/src/verifier.rs` enforces the receipt-size hard limit only when `mode == ValidateReceiptMode::NewReceipt`: [1](#0-0) 

But the runtime constructs `output_data_receivers` and appends them into an already-created receipt *after* the point where the new receipt was produced and sized. In `runtime/runtime/src/lib.rs`, when a receipt returns `ReturnData::ReceiptIndex` (i.e. `promise_return`), the code mutates an existing entry in `result.new_receipts` by extending its `output_data_receivers`, growing the receipt beyond whatever size it had when constructed/validated: [2](#0-1) 

Additionally, for plain `value_return`, only `max_length_returned_data` is checked in the VM logic host function — this bounds the *data* payload but not the resulting `DataReceipt`'s total borsh size once wrapped with predecessor/receiver ids and other framing: [3](#0-2) [4](#0-3) 

This is a **known, currently unpatched** issue in this codebase, explicitly acknowledged in comments and dedicated regression tests that assert the bug still reproduces: [5](#0-4) [6](#0-5) [7](#0-6) 

The workaround for this exact bug is visible in `congestion_control.rs`, where the size used for bandwidth/congestion admission is deliberately clamped to `max_receipt_size` to avoid receipts getting permanently stuck when they exceed the limit — a tacit admission that the size-limit invariant guaranteed by the "supposed" validation is not actually enforced end-to-end: [8](#0-7) 

### Impact Explanation
Oversized receipts bypass the state-witness size limits documented in `docs/misc/state_witness_size_limits.md`, which are designed to keep `ChunkStateWitness` bounded (~21 MiB) so that chunk validators can feasibly verify chunk application: [9](#0-8) 

If a chunk producer includes an oversized receipt (as this bug allows), and the resulting `ChunkStateWitness` grows unexpectedly, or if a legitimate chunk validator that recomputes limits diverges in output from an honest chunk producer's application (whether a receipt is treated as invalid/dropped vs. accepted), this can produce a state-root divergence between honest nodes, or the chunk endorsement/rejection paths documented in `docs/misc/state_witness_size_limits.md` ("If it turns out that some limits weren't respected, the validators will generate a different result of chunk application and they won't endorse the chunk"). It can also cause a receipt that violates the intended hard limit to be persisted and later re-processed with a different, more lenient validation mode (`ExistingReceipt`), meaning the invariant "all receipts ≤ max_receipt_size" that other subsystems (congestion control, bandwidth scheduler, `source_receipt_proofs` sizing) rely on is violated by design.

### Likelihood Explanation
This is triggerable by any account with a deployed contract and sufficient gas — no privileged role, validator, or network-level condition is needed. The existing test suite (`test-loop-tests/src/tests/max_receipt_size.rs`) already demonstrates concrete, deterministic proof-of-concept transactions (`max_receipt_size_promise_return_method1`, `max_receipt_size_value_return_method`) that trigger this from ordinary RPC-submitted transactions, and asserts (`assert_oversized_receipt_occurred`) that an oversized receipt is indeed accepted into the chain. This is not a theoretical analog — it is a confirmed, reproducible bug in this exact codebase.

### Recommendation
Enforce the `max_receipt_size` hard limit *after* all in-place mutations of a receipt are complete — specifically after `output_data_receivers` are appended in `runtime/runtime/src/lib.rs` (around lines 1152-1191), and immediately before/during `receipt_ids` assignment/persistence (around line 1193 onward). Re-validate (or re-check size on) any receipt whose `output_data_receivers` field is extended post-construction, and reject the transaction/action outcome if the resulting size exceeds `max_receipt_size`, rather than deferring to the lenient `ExistingReceipt` validation path. Remove the compensating clamp in `congestion_control.rs::try_forward` once the root cause is fixed, since the clamp only masks corrupted invariants at the congestion-control layer rather than preventing propagation of an oversized receipt.

### Proof of Concept
Use the existing reproduction already present in the codebase:
1. Deploy `rs_contract` (contains `max_receipt_size_promise_return_method1`/`method2`, `return_large_value`, `mark_test_completed`, `assert_test_completed`) — see `runtime/near-test-contracts/test-contract-rs/src/lib.rs` lines 1988-2104.
2. Submit a `FunctionCall` to `max_receipt_size_value_return_method` with `value_size` set to `max_receipt_size` (4,194,304 bytes), following the exact flow in `test_max_receipt_size_value_return`: [10](#0-9) 
3. Observe (as the test's `assert_oversized_receipt_occurred` helper does) that a receipt with borsh-encoded size greater than `max_receipt_size` is found in a produced block, i.e. it was accepted into the state transition rather than rejected with `ReceiptValidationError::ReceiptSizeExceeded`: [11](#0-10)

### Citations

**File:** runtime/runtime/src/verifier.rs (L687-696)
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L4578-4585)
```rust
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-213)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L350-428)
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

        block = prev_block;
    }
}

fn receipt_is_oversized(receipt: &Receipt, max_receipt_size: u64) -> bool {
    let receipt_size: u64 = borsh::object_length(receipt).unwrap().try_into().unwrap();
    if receipt_size > max_receipt_size {
        tracing::info!(%receipt_size, %max_receipt_size, "found receipt above max size");
        return true;
    }
    false
```

**File:** runtime/runtime/src/congestion_control.rs (L412-427)
```rust
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
