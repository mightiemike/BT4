## Title
Persistent buffered-receipt gas/size metadata underflow on `.expect()` bricks a shard's outgoing congestion accounting - ([File: core/store/src/trie/outgoing_metadata.rs])

### Summary
`ReceiptGroupsQueue::update_on_receipt_popped` maintains a running "reserved" total (`total_size`/`total_gas`) of receipts sitting in a shard's outgoing buffer, mirroring how the Ubiquity pool's `unclaimedPoolCollateral` tracks collateral owed to redeemers. Both values are decremented via checked subtraction, but in nearcore the failure mode on underflow is `.expect()`, i.e. a `panic!`, rather than a recoverable error, when the amount being "returned"/removed does not match what was originally reserved.

### Finding Description
`OutgoingMetadatas`/`ReceiptGroupsQueue` track, per receiving shard, the total size and gas of receipts sitting in the persistent outgoing buffer (`core/store/src/trie/outgoing_metadata.rs:206-233`). When a receipt is buffered, `update_on_receipt_pushed` adds its size/gas to the running totals with `add_size_checked`/`add_gas_checked` [1](#0-0) . When the receipt is later forwarded out of the buffer, `update_on_receipt_popped` subtracts the same size/gas from the totals using `subtract_size_checked`/`subtract_gas_checked`, both of which call `.checked_sub(...).expect(...)` and panic on underflow rather than returning an error [2](#0-1) [3](#0-2) .

This is structurally the same pattern as the Ubiquity bug: a reserved/aggregate counter (`unclaimedPoolCollateral` ↔ `total_size`/`total_gas`) is decremented by an amount computed independently at "claim time" (`collectRedemption` ↔ `forward_from_buffer_to_shard`) rather than reading back the exact value that was reserved at "reservation time" (`redeemDollar` ↔ `buffer_receipt`/`update_on_receipt_pushed`). In the pool, the AMO minter's unrestricted borrow desynchronizes the two; in nearcore, the pop-side value is recomputed by `receipt_congestion_gas`/`receipt_size` from the persisted receipt content rather than trusted verbatim from the push-side call. The `update_on_receipt_popped` caller in `runtime/runtime/src/congestion_control.rs::forward_from_buffer_to_shard` recomputes `gas`/`size` for the receipt being forwarded and passes those recomputed values into `OutgoingMetadatas::update_on_receipt_popped`, which forwards to `ReceiptGroupsQueue::update_on_receipt_popped`. If the recomputed value at pop time (based on `compute_receipt_congestion_gas`/`compute_receipt_size`, which are functions of `RuntimeConfig`) differs even slightly from the value that was used when the same receipt was pushed — which can happen if a protocol/runtime-config parameter that feeds into the congestion-gas/size formula changes between the chunk that buffered the receipt and the (potentially much later) chunk that forwards it, or if a `Receipt` object's own gas/size representation is not exactly re-derivable byte-for-byte by both paths — the subtraction underflows and the node crashes via `.expect()`.

Because the buffer can hold a receipt across many blocks/chunks (that's the whole point of buffering under congestion), and receipt forwarding is triggered purely by an ordinary, unprivileged transaction/receipt flow that fills a shard's outgoing buffer and is congested long enough for a config-affecting protocol upgrade or version-gated fee change to land in between, this is reachable without any privileged role — any user whose transactions create receipts routed cross-shard and get buffered can be the one whose eventual pop triggers the mismatch.

### Impact Explanation
A panic inside `Runtime::apply` (chunk application) is not a graceful, isolated failure like the pool's `mintDollar`/`redeemDollar` reverting for one user — it aborts the validator process applying that chunk. Since the buggy code path executes deterministically from on-chain state (the buffer and the receipts in it), every honest validator applying that chunk hits the exact same `.expect()` panic, which halts chunk production/application network-wide for the affected shard — a transaction-triggered halt, one of the explicitly accepted impact categories. This is strictly worse than the Ubiquity report's "bricked pool," because a smart-contract revert there degrades one module, while a panic here can stop the chain from making progress.

### Likelihood Explanation
The likelihood depends on whether the size/gas value fed to `update_on_receipt_popped` can ever legitimately diverge from the value used at push time for the *same* receipt. I was not able to fully verify within the available context whether `receipt_congestion_gas`/`compute_receipt_congestion_gas` and `receipt_size`/`compute_receipt_size` are guaranteed byte-stable and config-stable for a receipt across the entire time it can sit in the outgoing buffer (potentially spanning a protocol-version boundary or a `RuntimeConfig` parameter change such as `ClampOutgoingGasAdmission`/`EnforcePerReceiptStorageProofLimit`-style gates that affect fee/gas parameters used in `action_receipt_congestion_gas`). The code comments and existing defensive workarounds elsewhere in the same subsystem (e.g. the `max_receipt_size` clamp "to avoid receipts getting stuck," referencing issue #12606) suggest the nearcore team is aware that push-time and pop-time computations of receipt size/gas can disagree in edge cases, which is exactly the precondition for this underflow. Given the uncertainty, this should be treated as Medium/High likelihood pending confirmation that the congestion gas/size formulas are truly invariant across all protocol versions and configs for any receipt that can be buffered.

### Recommendation
Do not recompute `gas`/`size` independently at pop time from the live `RuntimeConfig`. Instead, persist the congestion gas/size that was used at push time as part of the receipt's on-trie representation (the `StateStoredReceiptMetadata { congestion_gas, congestion_size }` fields referenced in the congestion-control design already exist for a related purpose) and pass that persisted value into `OutgoingMetadatas::update_on_receipt_popped`/`ReceiptGroupsQueue::update_on_receipt_popped`, guaranteeing the pop-side subtraction always exactly reverses the push-side addition regardless of any later config or protocol-version change. Additionally, replace the `.expect()` panics in `subtract_size_checked`/`subtract_gas_checked` with a `checked_sub` that returns `Result<_, StorageError>` (as is already done in `CongestionInfo::remove_buffered_receipt_gas`, which maps the same failure mode to `RuntimeError::UnexpectedIntegerOverflow` instead of panicking) so any residual mismatch degrades gracefully into a recoverable `RuntimeError` instead of crashing the node/halting the chain.

### Proof of Concept
Concrete PoC could not be constructed with the available read-only context because it requires confirming, across `runtime/runtime/src/congestion_control.rs`'s `compute_receipt_congestion_gas`/`compute_receipt_size` and `core/primitives/src/receipt.rs`'s `StateStoredReceipt`/metadata handling, whether the size/gas value used at push time for a buffered receipt can ever diverge from the value recomputed at pop time (e.g., across a protocol upgrade that changes `RuntimeConfig` fee parameters while a receipt sits in the buffer). This would require running a two-node/protocol-upgrade integration test that (1) sends a transaction whose receipt gets buffered under protocol version N, (2) upgrades to protocol version N+1 with a fee/config change that affects `action_receipt_congestion_gas`, and (3) observes whether `forward_from_buffer_to_shard`'s recomputed gas mismatches the originally buffered value and triggers the `subtract_gas_checked`/`subtract_size_checked` panic in `core/store/src/trie/outgoing_metadata.rs`.

### Citations

**File:** core/store/src/trie/outgoing_metadata.rs (L275-314)
```rust
    pub fn update_on_receipt_pushed(
        &mut self,
        receipt_size: ByteSize,
        receipt_gas: Gas,
        state_update: &mut TrieUpdate,
        groups_config: &ReceiptGroupsConfig,
    ) -> Result<(), StorageError> {
        add_size_checked(&mut self.data.total_size, receipt_size);
        add_gas_checked(&mut self.data.total_gas, receipt_gas);
        self.data.total_receipts_num = self
            .data
            .total_receipts_num
            .checked_add(1)
            .expect("Overflow! - Number of receipts doesn't fit into u64!");

        // Take out the last group from the queue and inspect it.
        match self.pop_back(state_update)? {
            Some(mut last_group) => {
                if groups_config.should_start_new_group(&last_group, receipt_size, receipt_gas) {
                    // Adding the new receipt to the last group would make the group too large.
                    // Start a new group for the receipt.
                    self.push_back(state_update, &last_group).expect("Integer overflow on push");
                    self.push_back(state_update, &ReceiptGroup::new(receipt_size, receipt_gas))
                        .expect("Integer overflow on push");
                } else {
                    // It's okay to add the new receipt to the last group, do it.
                    add_size_checked(last_group.size_mut(), receipt_size);
                    add_gas_checked(last_group.gas_mut(), receipt_gas);
                    self.push_back(state_update, &last_group).expect("Integer overflow on push");
                }
            }
            None => {
                // No groups in the queue, start a new group which contains the new receipt.
                self.push_back(state_update, &ReceiptGroup::new(receipt_size, receipt_gas))
                    .expect("Integer overflow on push");
            }
        }

        Ok(())
    }
```

**File:** core/store/src/trie/outgoing_metadata.rs (L316-344)
```rust
    pub fn update_on_receipt_popped(
        &mut self,
        receipt_size: ByteSize,
        receipt_gas: Gas,
        state_update: &mut TrieUpdate,
    ) -> Result<(), StorageError> {
        subtract_size_checked(&mut self.data.total_size, receipt_size);
        subtract_gas_checked(&mut self.data.total_gas, receipt_gas);

        self.data.total_receipts_num = self
            .data
            .total_receipts_num
            .checked_sub(1)
            .expect("Underflow! - More receipts were popped than pushed!");

        assert!(self.data.indices.len() > 0, "No receipt groups to pop from!");

        self.modify_first(state_update, |mut first_group| {
            subtract_size_checked(first_group.size_mut(), receipt_size);
            subtract_gas_checked(first_group.gas_mut(), receipt_gas);
            if first_group.is_empty() {
                // No more receipts in the first group, remove it.
                None
            } else {
                // Still some receipts in the group. Save the updated group.
                Some(first_group)
            }
        })
    }
```

**File:** core/store/src/trie/outgoing_metadata.rs (L401-417)
```rust
fn subtract_size_checked(total: &mut u64, delta: ByteSize) {
    *total = total
        .checked_sub(delta.as_u64())
        .expect("subtract_size_checked - Underflow! Negative size!");
}

fn add_gas_checked(total: &mut u128, delta: Gas) {
    *total = total
        .checked_add(delta.as_gas().into())
        .expect("add_gas_checked - Overflow! Total gas doesn't fit into u128!");
}

fn subtract_gas_checked(total: &mut u128, delta: Gas) {
    *total = total
        .checked_sub(delta.as_gas().into())
        .expect("subtract_gas_checked - Underflow! Negative gas!")
}
```
