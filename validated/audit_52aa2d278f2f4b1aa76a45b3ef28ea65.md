### Title
Postponed and PromiseYield receipts bypass congestion-memory accounting, letting an unprivileged caller grow unbounded validator state without triggering NEP-539 backpressure - (File: `core/store/src/utils/mod.rs`)

### Summary
The Xen advisory describes an unprivileged guest causing unbounded memory allocation in `xenstored` because several code paths (buffered responses, watch events, per-transaction node creation) are not subject to a common resource accounting/limit. The nearcore analog is NEP-539 congestion control: it is supposed to track "delayed, buffered, postponed, or yielded" receipt bytes in a single `CongestionInfo.receipt_bytes` counter and throttle (or reject) new transactions once that counter crosses a threshold. However, the actual write paths for **postponed** action receipts and **PromiseYield** receipts never call into that accounting.

### Finding Description
`CongestionInfo::add_receipt_bytes` / `add_buffered_receipt_gas` are only invoked from the two paths that are explicitly congestion-aware:
- `DelayedReceiptQueueWrapper::push` [1](#0-0) 
- `ReceiptSinkV2WithInfo::buffer_receipt` [2](#0-1) 

But the protocol spec explicitly documents that `receipt_bytes` is supposed to be "the borsh size of all delayed/buffered/postponed/yielded receipts" [3](#0-2) .

In practice, the two other queue types that hold full `Receipt` payloads in trie state - postponed action receipts and parked PromiseYield receipts - are written with plain, un-accounted `set()` calls:
- `set_postponed_receipt` [4](#0-3) 
- `set_promise_yield_receipt` [5](#0-4) 

Neither function (nor their call sites in `Runtime::process_action_receipt`/`process_receipt`) touches `CongestionInfo`. This is consistent with `compute_receipt_congestion_gas`, which explicitly charges **zero** congestion gas for `Data`, `PromiseYield`, `PromiseResume`, and `GlobalContractDistribution` receipts "the MVP does not charge them (data/postponed costs would require extra trie lookups)" [6](#0-5) . So both the gas dimension and (unlike what the doc claims) the byte/memory dimension of congestion silently exclude postponed and promise-yield receipts.

A single submitted transaction can trivially create many `Action` receipts with unmet `input_data_ids` (up to `max_number_input_data_dependencies` per receipt), each of which the runtime persists as a full postponed `Receipt` in `TrieKey::PostponedReceipt` via `set_postponed_receipt`, and separately as many `PromiseYield` receipts (each up to `max_yield_payload_size`) via `set_promise_yield_receipt`. Because neither call path feeds `CongestionInfo.receipt_bytes`, the shard's `congestion_level` (memory dimension, `receipt_bytes / max_congestion_memory_consumption`) never reflects this growth, so `shard_accepts_transactions`/`reject_tx_congestion_threshold` never fires to protect the shard [7](#0-6) , and the "red light" mechanism designed to bound validator memory usage (documented in `receipt-congestion.md`) is not engaged for this class of state growth.

### Impact Explanation
This produces a class of unbounded-memory-growth similar to the Xen advisory: an unprivileged transaction signer (no special privilege, just gas to pay) can inflate a validator's postponed/promise-yield trie state and the associated hot-state/working-set memory beyond what the congestion-control design assumes is possible, while the shard-level congestion signal used to protect the network (rejecting new transactions, throttling forwarding) stays blind to it. This weakens the DoS-resistance guarantee NEP-539 is built to provide, though it is mitigated by normal per-account storage staking (the receiving account must have enough locked balance to cover the extra `PostponedReceipt`/`PromiseYieldReceipt` state) and by the receiver-scoped nature of both structures.

### Likelihood Explanation
Reachable purely through standard, permissionless mechanisms: an ordinary `FunctionCall` action that creates receipts with unmet data dependencies (postponed receipts) or that calls `promise_yield_create` (PromiseYield receipts), both of which any account holder can trigger. No validator or network-layer compromise is required.

### Recommendation
Route `set_postponed_receipt`/`remove_postponed_receipt` and `set_promise_yield_receipt`/`remove_promise_yield_receipt` through the same `CongestionInfo::add_receipt_bytes`/`remove_receipt_bytes` accounting used by the delayed queue and outgoing buffers, so the memory-congestion dimension (and ideally a nonzero gas cost) reflects the true trie footprint of postponed and promise-yield receipts, matching the behavior documented in `receipt-congestion.md`/`cross-shard-congestion.md`.

### Proof of Concept
1. Deploy a contract whose method issues an `Action` receipt with several `input_data_ids` that are never (or slowly) satisfied — this is stored as a `PostponedReceipt` via `set_postponed_receipt` (`core/store/src/utils/mod.rs:120`), and/or repeatedly calls `promise_yield_create`, which is stored via `set_promise_yield_receipt` (`:200`).
2. Submit many such transactions/calls from a single unprivileged account (bounded only by gas per chunk and the account's storage-staking balance, not by congestion control).
3. Observe that `CongestionInfo.receipt_bytes` reported in the chunk header (`apply_result.congestion_info`) does not increase in proportion to the actual postponed/promise-yield trie growth, verified via `test_congestion_delayed_receipts_accounting`-style assertions (`runtime/runtime/src/tests/apply.rs:3388-3428`) extended to postponed/yield receipts instead of delayed ones — the counter would remain unaffected while trie state size grows.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L466-501)
```rust
    fn buffer_receipt(
        &mut self,
        receipt: Receipt,
        size: u64,
        gas: Gas,
        state_update: &mut TrieUpdate,
        shard: ShardId,
        use_state_stored_receipt: bool,
    ) -> Result<(), RuntimeError> {
        let receipt = match use_state_stored_receipt {
            true => {
                let metadata =
                    StateStoredReceiptMetadata { congestion_gas: gas, congestion_size: size };
                let receipt = StateStoredReceipt::new_owned(receipt, metadata);
                let receipt = ReceiptOrStateStoredReceipt::StateStoredReceipt(receipt);
                receipt
            }
            false => ReceiptOrStateStoredReceipt::Receipt(std::borrow::Cow::Owned(receipt)),
        };

        self.own_congestion_info.add_receipt_bytes(size)?;
        self.own_congestion_info.add_buffered_receipt_gas(gas)?;

        if receipt.should_update_outgoing_metadatas() {
            self.outgoing_metadatas.update_on_receipt_pushed(
                shard,
                ByteSize::b(size),
                gas,
                state_update,
            )?;
        }

        self.outgoing_buffers.to_shard(shard).push_back(state_update, &receipt)?;
        self.stats.buffered_receipts.entry(shard).or_default().add_receipt(size, gas);
        Ok(())
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L838-866)
```rust
    pub(crate) fn push(
        &mut self,
        trie_update: &mut TrieUpdate,
        receipt: &Receipt,
        apply_state: &ApplyState,
    ) -> Result<(), RuntimeError> {
        let config = &apply_state.config;

        let gas = compute_receipt_congestion_gas(&receipt, &config)?;
        let size = compute_receipt_size(&receipt)? as u64;

        // TODO It would be great to have this method take owned Receipt and
        // get rid of the Cow from the Receipt and StateStoredReceipt.
        let receipt = match config.use_state_stored_receipt {
            true => {
                let metadata =
                    StateStoredReceiptMetadata { congestion_gas: gas, congestion_size: size };
                let receipt = StateStoredReceipt::new_borrowed(receipt, metadata);
                ReceiptOrStateStoredReceipt::StateStoredReceipt(receipt)
            }
            false => ReceiptOrStateStoredReceipt::Receipt(Cow::Borrowed(receipt)),
        };

        self.new_delayed_gas = self.new_delayed_gas.checked_add(gas).ok_or(IntegerOverflowError)?;
        self.new_delayed_bytes =
            self.new_delayed_bytes.checked_add(size).ok_or(IntegerOverflowError)?;
        self.queue.push_back(trie_update, &receipt)?;
        Ok(())
    }
```

**File:** protocol-model/spec/cross-shard-congestion.md (L42-47)
```markdown
- **`CongestionInfo` / `CongestionInfoV1`** — `core/primitives/src/congestion_info.rs:187`,
  `:460` — per-shard, versioned, committed in the chunk header. `V1` tracks
  `delayed_receipts_gas: u128`, `buffered_receipts_gas: u128`, `receipt_bytes: u64`
  (borsh size of all delayed/buffered/postponed/yielded receipts), and
  `allowed_shard: u16` (the one shard permitted to forward to us when we are fully
  congested).
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

**File:** core/store/src/utils/mod.rs (L120-127)
```rust
pub fn set_postponed_receipt(state_update: &mut TrieUpdate, receipt: &Receipt) {
    assert!(matches!(receipt.versioned_receipt(), VersionedReceiptEnum::Action(_)));
    let key = TrieKey::PostponedReceipt {
        receiver_id: receipt.receiver_id().clone(),
        receipt_id: *receipt.receipt_id(),
    };
    set(state_update, key, receipt);
}
```

**File:** core/store/src/utils/mod.rs (L200-212)
```rust
pub fn set_promise_yield_receipt(state_update: &mut TrieUpdate, receipt: &Receipt) {
    match receipt.versioned_receipt() {
        VersionedReceiptEnum::PromiseYield(action_receipt) => {
            assert!(action_receipt.input_data_ids().len() == 1);
            let key = TrieKey::PromiseYieldReceipt {
                receiver_id: receipt.receiver_id().clone(),
                data_id: action_receipt.input_data_ids()[0],
            };
            set(state_update, key, receipt);
        }
        _ => unreachable!("Expected PromiseYield receipt"),
    }
}
```

**File:** core/primitives/src/congestion_info.rs (L113-124)
```rust
    /// How much gas we accept for executing new transactions going to any
    /// uncongested shards.
    pub fn process_tx_limit(&self) -> Gas {
        mix_gas(self.config.max_tx_gas, self.config.min_tx_gas, self.incoming_congestion())
    }

    /// Whether we can accept new transaction with the receiver set to this shard.
    ///
    /// If the shard doesn't accept new transaction, provide the reason for
    /// extra debugging information.
    pub fn shard_accepts_transactions(&self) -> ShardAcceptsTransactions {
        let incoming_congestion = self.incoming_congestion();
```
