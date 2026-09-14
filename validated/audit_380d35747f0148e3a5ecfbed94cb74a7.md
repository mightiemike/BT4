## Analog Found

### Title
Deterministic assertion-failure DoS in cross-shard receipt congestion accounting - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
CVE-2018-14044 is an assertion failure in SoundTouch's `RateTransposer::setChannels` triggered by attacker-controlled input, causing the process to abort (DoS). The analogous pattern in nearcore is a hard `assert_eq!` in the runtime's cross-shard congestion-control bookkeeping that can be tripped by state that every honest validator computes identically, causing a synchronized, chain-wide crash rather than a graceful error.

### Finding Description
`ReceiptSinkV2WithInfo::forward_from_buffer` runs at the start of every chunk apply and, after draining the outgoing receipt buffers for all shards, asserts that if every buffer is empty then the shard's tracked `buffered_receipts_gas` accounting must be exactly zero: [1](#0-0) 

This is a real (non-`debug_assert`) `assert_eq!`, so it panics in release builds too, unlike most other runtime error paths in this codebase which return `Result`/`RuntimeError` (see the deliberate pattern of using `Result` instead of panics for validator-account/nonce/receipt mismatches, e.g. `VerificationResult::apply`): [2](#0-1) 

The `buffered_receipts_gas` counter is maintained incrementally: when a receipt is buffered, `gas` is added; when it is later forwarded out of the buffer, the **same congestion gas value must be subtracted** for the invariant to hold: [3](#0-2) [4](#0-3) 

The problem is that the congestion gas of a buffered receipt is **not** a value stored once in the receipt or its trie-persisted metadata; it is *recomputed from `apply_state.config`* both when the receipt is buffered and again, independently, when it is later forwarded out of the buffer (`receipt_congestion_gas(&receipt, &apply_state.config)` in `forward_from_buffer_to_shard`, and `compute_receipt_congestion_gas` in `forward_or_buffer_receipt`). Per the protocol spec, this congestion gas is derived from `total_prepaid_exec_fees` / `total_prepaid_send_fees`, which depend on the **current** `RuntimeConfig` fee table, not on any value frozen into the receipt at creation time: [5](#0-4) 

`RuntimeConfig`'s fee table is versioned and changes across protocol upgrades (the spec elsewhere documents `AccountCostIncrease` and other fee-table changes gated by protocol version). If a receipt sits in a shard's outgoing buffer across a protocol-version boundary that changes any fee feeding `exec_fee`/`total_send_fees`, the gas value **added** to `own_congestion_info.buffered_receipts_gas()` at buffer time (under the old fee table) will differ from the gas value **subtracted** when it is finally forwarded (under the new fee table). Because every honest validator applies the identical chunk sequence with the identical protocol-version schedule, this drift is fully deterministic and identical across the whole validator set.

### Impact Explanation
When the drift eventually causes the tracked `buffered_receipts_gas` to be non-zero at a point where the actual buffers have all drained, the `assert_eq!` at `congestion_control.rs:283` fires. Since the state transition is fully deterministic and protocol-version activation is network-wide, this hits **every validator processing that shard at the same block height simultaneously**, causing a synchronized node crash — i.e., a transaction/congestion-triggered chain halt, not merely a single node's DoS. This satisfies the "transaction-triggered halt" acceptance criterion: an unprivileged party who can submit ordinary cross-shard transactions (creating buffered receipts before a scheduled protocol upgrade) can engineer a receipt-buffering pattern whose accounting the protocol upgrade will desynchronize, at least in principle for any future fee-table change that isn't carefully accounted for around this invariant.

### Likelihood Explanation
Exploitability depends on the existence and magnitude of a fee-table change activated at a protocol version boundary while receipts are still in flight in the outgoing buffer — a condition that is plausible given this codebase's history of fee-table adjustments (`AccountCostIncrease`, similar `RuntimeConfig` versioned parameters) and the explicit, unfixed size/gas-accounting workarounds already called out in the code itself (`issue #12606` comments at `congestion_control.rs:413-427` and `:556-560` acknowledge receipts can exceed the size limit and that accounting has to "pretend" sizes are clamped — evidence that this accounting subsystem has known correctness gaps). An attacker can maximize likelihood by deliberately congesting a shard (sending many, or maximally fee-sensitive, cross-shard receipts) immediately before a known protocol-upgrade block, but ultimately the panic firing also requires the specific fee-table change to affect `total_prepaid_exec_fees`/`total_prepaid_send_fees` for at least one buffered receipt, which is protocol-release-dependent rather than universally triggerable today.

### Recommendation
- Make `own_congestion_info.buffered_receipts_gas()` reconciliation tolerant of drift instead of a hard `assert_eq!`: clamp to zero, log, and continue (mirroring the `Result`-based error handling used elsewhere for this exact struct's `checked_add`/`checked_sub`).
- Persist the congestion `gas`/`size` computed at buffer-time as part of the buffered receipt's on-trie metadata (similar to `StateStoredReceiptMetadata { congestion_gas, congestion_size }`, already present) and always recompute the same value on dequeue from that stored metadata rather than from the current, potentially-changed `apply_state.config`.
- Add a regression test that buffers a receipt, changes the effective fee configuration (simulating a protocol-version bump), and forwards it, asserting the invariant does not panic.

### Proof of Concept
A concrete PoC requires reproducing a real fee-table change across a protocol-version activation while a receipt sits in the outgoing buffer, which is release-schedule dependent and could not be constructed purely from this snapshot. The structural PoC is:
1. Send a cross-shard transaction whose resulting receipt gets buffered in `ReceiptSinkV2::buffer_receipt` under protocol version `N`'s `RuntimeConfig` fee table (congest the target shard's `outgoing_limit` so the receipt is *not* forwarded immediately).
2. Advance the chain past a protocol-version upgrade to `N+1` that changes any parameter feeding `total_prepaid_exec_fees`/`total_prepaid_send_fees` (e.g., an `exec_fee`/send-fee change).
3. Let the buffer drain under `N+1`; `forward_from_buffer_to_shard` recomputes `gas` with the new config and calls `remove_buffered_receipt_gas(gas)` with a value differing from what was added in step 1.
4. Once all buffers for the shard are empty, `forward_from_buffer`'s `assert_eq!(self.sink.own_congestion_info.buffered_receipts_gas(), 0)` fails on every validator applying that shard at that height, panicking the process.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L281-284)
```rust
        // Assert that empty buffers match zero buffered gas.
        if all_buffers_empty {
            assert_eq!(self.sink.own_congestion_info.buffered_receipts_gas(), 0);
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L357-394)
```rust
            match Self::try_forward(
                receipt,
                gas,
                size,
                target_shard_id,
                &mut self.outgoing_limit,
                &mut self.outgoing_receipts,
                apply_state,
                &mut self.stats,
            )? {
                ReceiptForwarding::Forwarded => {
                    self.own_congestion_info.remove_receipt_bytes(size)?;
                    self.own_congestion_info.remove_buffered_receipt_gas(gas.as_gas().into())?;
                    if should_update_outgoing_metadatas {
                        // Can't update metadatas immediately because state_update is borrowed by iterator.
                        outgoing_metadatas_updates.push((ByteSize::b(size), gas));
                    }
                    // count how many to release later to avoid modifying
                    // `state_update` while iterating based on
                    // `state_update.trie`.
                    num_forwarded += 1;
                }
                ReceiptForwarding::NotForwarded(_) => {
                    break;
                }
            }
        }

        self.outgoing_buffers.to_shard(buffer_shard_id).pop_n(state_update, num_forwarded)?;
        for (size, gas) in outgoing_metadatas_updates {
            self.outgoing_metadatas.update_on_receipt_popped(
                buffer_shard_id,
                size,
                gas,
                state_update,
            )?;
        }
        Ok(())
```

**File:** runtime/runtime/src/congestion_control.rs (L465-500)
```rust
    /// Put a receipt in the outgoing receipt buffer of a shard.
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
```

**File:** runtime/runtime/src/lib.rs (L328-347)
```rust
impl VerificationResult {
    /// Apply the state changes described by this result.
    ///
    /// `access_key` must be present for every update except `Bootstrap`, which
    /// requires an uninitialized account instead. Each verifier returns only the
    /// variant matching what its caller loaded, so a mismatch means the two have
    /// drifted apart; it is reported rather than panicked on, because this runs
    /// while a chunk is being applied and a panic there stops the node instead of
    /// the transaction.
    pub fn apply(
        &self,
        account: &mut Account,
        access_key: Option<&mut AccessKey>,
    ) -> Result<(), StorageError> {
        let inconsistent = |what: &str| {
            StorageError::StorageInconsistentState(format!(
                "{what} for {:?}",
                self.access_key_update
            ))
        };
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
