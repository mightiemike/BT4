### Title
Unchecked receipt-size specification: `value_return` enforces only `max_length_returned_data`, letting a single `FunctionCall` create a `Receipt` that exceeds `max_receipt_size` — ([File: runtime/near-vm-runner/src/wasmtime_runner/logic.rs])

### Summary
The runtime's documented specification requires that **every** receipt stay under `max_receipt_size` so that `ChunkStateWitness` size stays bounded and forwarding/buffering logic behaves correctly. The enforcement point for a contract's returned value, `value_return`, only checks the payload against `max_length_returned_data`, not against the actual serialized size of the `Receipt` it will be wrapped into. Because `max_length_returned_data` and `max_receipt_size` are configured to the same numeric value (4 MiB), any value at the length limit produces a serialized `DataReceipt`/`Receipt` that exceeds `max_receipt_size` once envelope fields (`predecessor_id`, `receiver_id`, `receipt_id`, `data_id`, borsh framing) are added.

### Finding Description
`value_return` validates only the returned-value length against `max_length_returned_data`: [1](#0-0) 

That value is later wrapped into a `DataReceipt` and then a full `Receipt` (with `predecessor_id`, `receiver_id`, `receipt_id`) in the runtime's "Generating outgoing data" step: [2](#0-1) 

The specification's actual size ceiling, `max_receipt_size`, is only checked inside `validate_receipt` under `ValidateReceiptMode::NewReceipt`: [3](#0-2) 

but the data-receipt-specific validator, `validate_data_receipt`, re-checks only `max_length_returned_data`, not the enclosing receipt's total size: [4](#0-3) 

The codebase itself documents that this exact gap is exploitable and unresolved, introducing a permissive `ExistingReceipt` validation mode specifically to tolerate oversized receipts that already got created: [5](#0-4) 

Downstream, `try_forward` in the congestion-control/receipt-sink path explicitly works around the same bug by clamping any oversized receipt's `size` to `max_receipt_size` for admission-limit accounting, rather than rejecting it: [6](#0-5) 

This is precisely the audit bug-class from the report: a specification requirement ("must fulfil size ≤ N") stated as an invariant but not actually enforced at the point of construction/entry — here, `value_return`/data-receipt creation — leaving enforcement to downstream best-effort workarounds. This is already tracked as nearcore issue #12606 and reproduced by an existing test: [7](#0-6) 

### Impact Explanation
An oversized receipt violates a documented protocol invariant that other subsystems assume holds (bandwidth scheduling, congestion control, state-witness size bounds). The current mitigations (`ExistingReceipt` mode tolerance, size clamping in `try_forward`) are explicit acknowledgements that the invariant is broken; any future code path that assumes `max_receipt_size` is a hard ceiling (e.g. witness-size accounting, or chunk producers computing exact bandwidth/size budgets) can be pushed into an inconsistent state or, in a worst case, a state-witness/size-budget divergence between honest nodes if one node's tolerance logic disagrees with another's. This is reachable purely from an unprivileged `FunctionCall` transaction (or nested promise) that returns a maximal-length value, no special privileges required.

### Likelihood Explanation
High likelihood of triggering the oversized-receipt condition itself (a single `return_large_value`-style contract call at `max_length_returned_data` bytes reliably reproduces it, as shown by the existing regression test). The class of resulting harm (protocol-invariant violation tolerated only via ad-hoc clamping) is a known, currently-open issue rather than a purely theoretical one.

### Recommendation
Enforce the `max_receipt_size` bound at the point where the receipt is actually constructed (i.e., account for envelope overhead in `value_return`/data-receipt creation, or re-validate the fully-assembled `Receipt` immediately after `receipt_id` assignment and before it enters `forward_or_buffer_receipt`), rather than relying on a permissive `ExistingReceipt` validation mode and size-clamping workarounds in `try_forward` to paper over receipts that were allowed to exceed the specified limit.

### Proof of Concept [8](#0-7) [9](#0-8) 

Any account calls `max_receipt_size_value_return_method`, which in turn calls `return_large_value` with `value_size = max_receipt_size (4_194_304)`. The value passes `value_return`'s `max_length_returned_data` check, but the resulting `DataReceipt`/`Receipt` (value + `data_id` + `predecessor_id` + `receiver_id` + borsh framing) exceeds `max_receipt_size`, reproducing the documented, currently-unresolved size-limit bypass (nearcore issue #12606).

### Citations

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

**File:** runtime/runtime/src/lib.rs (L1171-1189)
```rust
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-216)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
#[test]
fn test_max_receipt_size_value_return() {
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L236-250)
```rust
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
```

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L2053-2065)
```rust
/// Returns a value of size "value_size".
/// Accepts json args, e.g {"value_size": 1000}
#[no_mangle]
pub unsafe fn return_large_value() {
    input(0);
    let mut args = vec![0u8; register_len(0) as usize];
    read_register(0, args.as_mut_ptr());
    let input_args_json: serde_json::Value = serde_json::from_slice(&args).unwrap();
    let args_size = input_args_json["value_size"].as_u64().unwrap();

    let large_value = vec![0u8; args_size as usize];
    value_return(large_value.len() as u64, large_value.as_ptr() as u64);
}
```
