### Title
Receipt duplication via unbounded fan-out of `output_data_receivers` in Promise API - (File: `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`, `runtime/runtime/src/receipt_manager.rs`, `runtime/runtime/src/lib.rs`)

### Summary
`promise_and` and `create_action_receipt` never deduplicate the receipt/promise indices supplied by the calling contract. A contract can register the same source promise as a dependency for many different callback receipts (or the same promise index repeatedly through `promise_and`), so a single source receipt ends up with many `output_data_receivers` entries. When that source receipt finishes executing, `runtime/runtime/src/lib.rs` clones its full return value once **per** `output_data_receivers` entry and emits one independent outgoing `Data` receipt for each, without any per-copy cost that scales with the number of duplicates. This is structurally the same bug class as the PocketMine-MP report: client/attacker-controlled duplicate identifiers cause the server to repeat expensive send/clone work multiple times for what should be a single logical item.

### Finding Description
`promise_and` reads `promise_idx_count` promise indices from guest memory and pushes every resolved `receipt_idx` into `receipt_dependencies` with no uniqueness check: [1](#0-0) 

`create_action_receipt` in the receipt manager likewise accepts `receipt_indices` as-is, without checking that the same source receipt index isn't referenced multiple times, and for every entry pushes a fresh `DataReceiver` onto that source receipt's `output_data_receivers`: [2](#0-1) 

There is no limit tying the number of `output_data_receivers` attached to one receipt to anything other than the caller's willingness to keep issuing `promise_then`/`promise_batch_then`/`promise_and` calls that all reference the same underlying promise (the only bound enforced is `max_number_input_data_dependencies`, which caps how many dependencies one *callback* can join, not how many distinct callbacks may depend on the same *source*).

When the source receipt executes, the runtime iterates `output_data_receivers` and, for each entry, clones the entire returned value into a brand-new `Data` receipt: [3](#0-2) 

Each of these `Data` receipts is a full, independent outgoing receipt going through `ReceiptSink::forward_or_buffer_receipt` and the bandwidth/congestion pipeline. Crucially, congestion control explicitly assigns **zero congestion gas** to `Data` receipts (the fee/gas model does not scale with the number of duplicate copies produced), so the fan-out amplification bypasses the gas-based admission control that limits every other receipt kind: [4](#0-3) 

This mirrors the PocketMine-MP root cause precisely: the server (runtime) trusts a caller-supplied list of identifiers (`promise_idx`/`receipt_idx`) to be logically distinct "requests," and repeats an expensive operation (cloning and forwarding a large return payload) once per duplicate entry, with no dedup and no cost scaling for the duplication itself.

### Impact Explanation
A single `FunctionCall` action can cause a source promise's result (up to the configured `max_length_returned_data`, observed as multi-MB in `test_promise_input_size_limit_fails_callback`) to be cloned and emitted as N separate `Data` receipts, each carrying an independent copy of the same payload, to N different receiver accounts across potentially N different shards: [5](#0-4) 

Because `Data` receipts are congestion-gas-free, this multiplies outgoing receipt bytes, buffer occupancy, receipt-to-tx bookkeeping, and cross-shard forwarding work substantially beyond what the attacker's own gas payment for creating the (cheap, small) `promise_then`/`promise_batch_then` calls reflects. This is a receipt-duplication / gas-bypass pattern: the same logical execution result is turned into multiple independently-accounted receipts whose downstream processing cost is not gated by the fee model that is supposed to price receipt/data propagation.

### Likelihood Explanation
Reachable from a single unprivileged `FunctionCall` transaction with no special permissions — any contract deployer/caller can invoke `promise_batch_create`, repeatedly call `promise_then`/`promise_batch_then` against the same promise index (or use `promise_and` with duplicate indices) to attach many `output_data_receivers` to one source receipt, and then have that source return a large value. No validator, node, or peer collusion is required. The exact multiplication factor achievable in practice, and whether the fee schedule for `new_data_receipt_base` charges anything proportional to `output_data_receivers.len()` at the time the *source* receipt executes (as opposed to at each `promise_then` call, which only pays for creating the small callback skeleton), could not be fully confirmed from the available code and would need direct verification of `RuntimeFeesConfig`/`transfer_cost` computation for data receipts in a live session.

### Recommendation
- Deduplicate receipt indices passed into `promise_and` and into `create_action_receipt`'s `receipt_indices`, or explicitly track and cap the number of `output_data_receivers` a single source receipt may accumulate (independent of `max_number_input_data_dependencies`, which only bounds the callback side).
- When computing gas/fees for a receipt with `output_data_receivers`, charge a per-receiver cost proportional to the returned data size, so that fan-out is priced instead of free.
- Re-evaluate whether `Data` receipts should continue to be assigned zero congestion gas given that their size (and thus downstream cost) can be arbitrarily amplified by this fan-out pattern.

### Proof of Concept
1. Deploy a contract, similar to `near-test-contracts/test-contract-rs`, that:
   - Creates one promise `P` calling a method that returns a value close to `max_length_returned_data`.
   - Calls `promise_then(P, ...)` (or joins `P` via `promise_and` with duplicate indices) many times, each targeting a distinct lightweight `noop` callback on a different account, up to the practical batch-action limits.
2. Submit this as a single transaction.
3. Observe (as in `runtime/runtime/tests/test_async_calls.rs`'s receipt-graph assertions) that the source receipt for `P`'s completion produces one full-size `Data` receipt per attached `output_data_receivers` entry: [6](#0-5) 
   each counted as zero congestion gas, multiplying the outgoing receipt byte volume and cross-shard forwarding work far beyond the gas paid for the triggering transaction.

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L2408-2432)
```rust
    let mut receipt_dependencies = vec![];
    for promise_idx in promise_indices {
        let promise = ctx
            .promises
            .get(promise_idx as usize)
            .ok_or(HostError::InvalidPromiseIndex { promise_idx })?;
        match &promise {
            Promise::Receipt(receipt_idx) => {
                receipt_dependencies.push(*receipt_idx);
            }
            Promise::NotReceipt(receipt_indices) => {
                receipt_dependencies.extend(receipt_indices.clone());
            }
        }
        // Checking this in the loop to prevent abuse of too many joined vectors.
        if receipt_dependencies.len() as u64
            > ctx.config.limit_config.max_number_input_data_dependencies
        {
            return Err(HostError::NumberInputDataDependenciesExceeded {
                number_of_input_data_dependencies: receipt_dependencies.len() as u64,
                limit: ctx.config.limit_config.max_number_input_data_dependencies,
            }
            .into());
        }
    }
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

**File:** runtime/runtime/src/lib.rs (L1152-1191)
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

**File:** protocol-model/spec/cross-shard-congestion.md (L149-156)
```markdown
The **congestion cost** of a receipt is defined by `compute_receipt_congestion_gas`
(`congestion_control.rs:678`): for action receipts it sums prepaid exec fees, the
`new_action_receipt` fee, prepaid send fees, and attached function-call gas
(`action_receipt_congestion_gas`, `:716`). `Data`, `PromiseYield`, `PromiseResume`,
and `GlobalContractDistribution` all count as **zero** congestion gas
(`:687-712`) — the MVP does not charge them (data/postponed costs would require extra
trie lookups). Size is the borsh length of the whole receipt (`compute_receipt_size`,
`:964`).
```

**File:** test-loop-tests/src/tests/promise_input_size_limit.rs (L41-83)
```rust
    // Two callee contracts each return 2.5 MiB (individually below `max_length_returned_data`
    // = 4 MiB, so each data receipt is valid), joined into a single callback whose
    // combined promise inputs (~5 MiB) exceed the 4 MiB limit.
    let value_size = 2_500_000u64;
    let callee_gas = 110_000_000_000_000u64;
    let callback_gas = 20_000_000_000_000u64;
    let args = serde_json::json!([
        {
            "create": {
                "account_id": account,
                "method_name": "return_large_value",
                "arguments": {"value_size": value_size},
                "amount": "0",
                "gas": callee_gas,
            },
            "id": 0,
        },
        {
            "create": {
                "account_id": account,
                "method_name": "return_large_value",
                "arguments": {"value_size": value_size},
                "amount": "0",
                "gas": callee_gas,
            },
            "id": 1,
        },
        {"and": [0, 1], "id": 2},
        {
            "then": {
                "promise_index": 2,
                "account_id": account,
                "method_name": "return_large_value",
                "arguments": {"value_size": 0},
                "amount": "0",
                "gas": callback_gas,
            },
            "id": 3,
            // Return the callback promise so the transaction's final status
            // reflects the callback receipt's outcome.
            "return": true,
        },
    ]);
```
