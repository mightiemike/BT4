This confirms the read path is properly integrated into normal BlockSTM validation. `LatestView::get_aggregator_v1_state_value` (the AggregatorV1 legacy path used inside `switch`/`read`) routes through `self.get_resource_state_value(state_key, None)` [1](#0-0) , which is the same normal resource read path used for ordinary Move resources — captured as a `DataRead` and checked by `validate_data_reads`/`validate_data_reads_impl` at commit/validation time [2](#0-1) .

Based on this analysis:

**No vulnerability found for this question.**

Reasoning:

1. **No cross-transaction mutable references exist.** Move/AptosVM transactions do not share references across transaction boundaries. Each transaction gets a fresh VM session and re-deserializes the `OptionalAggregator`-containing resource from the executor's speculative view for that transaction index. There is no mechanism by which txn B could hold a "stale reference obtained before a switch happened in a different transaction" — the premise doesn't map onto how the block-executor's `LatestView`/MVHashMap model works. `switch` mutates the field in place inside a normal Move resource [3](#0-2) , and that mutation is persisted as an ordinary resource write, keyed by the resource's `StateKey`.

2. **`add`/`sub` dispatch on the freshly-read struct, not on stale data.** `optional_aggregator::add`/`sub` check `optional_aggregator.aggregator.is_some()` on the value they hold *in the current transaction's session* [4](#0-3) . To get that value, the transaction must first read the parent resource, which is a normal captured data read tied to that resource's `StateKey`. If txn A (the `switch`) writes to that same key, any txn B reading it is subject to the same read/write conflict-detection machinery that governs all resource reads in BlockSTM.

3. **Read/write conflict detection is symmetric and general, not special-cased per-aggregator-representation.** `validate_data_reads_impl` compares a txn's captured `DataRead` against the current `MVHashMap` value via `compare_data_reads`, requiring `DataReadComparison::Contains` [2](#0-1) ; any mismatch (e.g., due to a resource write from an earlier-ordered transaction like `switch`) fails validation and the reading transaction is re-executed. This applies uniformly regardless of whether the resource happens to embed an `Option<Aggregator>`/`Option<Integer>` pair.

4. **`AggregatorChangeV1::MaterializedDelta` only applies to raw AggregatorV1 add/sub deltas that were never read** (`is_write = new_aggregators.contains(id) || read_aggregators.contains(id)`) [5](#0-4) . Since `switch` explicitly calls `aggregator::read` before `aggregator::destroy`, the aggregator is marked as read, so its change is not folded as a stale delta but overwritten by `AggregatorChangeV1::Delete` at commit time [6](#0-5) . Any transaction that concurrently applies an `add`/`sub` delta to the same raw aggregator's state key would produce a write to that key too; both changes are resolved on the same `StateKey` under standard MVHashMap versioning, not as an unvalidated stale reference.

The exploit's premise — that a stale `&mut OptionalAggregator` reference from txn A could persist into txn B's execution and bypass read-set validation — does not correspond to any real code path in this codebase. Resource reads (including the `OptionalAggregator`'s embedded `Aggregator`/`Integer` state) are always freshly obtained per-transaction through `LatestView`, and are subject to the same generic, well-tested `validate_data_reads` machinery as any other resource. No path was found where a switched resource's change could be committed underneath a conflicting stale read without triggering re-execution.

### Citations

**File:** aptos-move/block-executor/src/view.rs (L1837-1845)
```rust
impl<T: Transaction, S: TStateView<Key = T::Key>> TAggregatorV1View for LatestView<'_, T, S> {
    type Identifier = T::Key;

    fn get_aggregator_v1_state_value(
        &self,
        state_key: &Self::Identifier,
    ) -> PartialVMResult<Option<StateValue>> {
        self.get_resource_state_value(state_key, None)
    }
```

**File:** aptos-move/block-executor/src/captured_reads.rs (L882-908)
```rust
    fn validate_data_reads_impl<'a>(
        &'a self,
        iter: impl Iterator<Item = (&'a T::Key, &'a DataRead<T::Value>)>,
        data_map: &VersionedData<T::Key, ValueWithLayout<T::Value>>,
        idx_to_validate: TxnIndex,
    ) -> bool {
        use MVDataError::*;
        use MVDataOutput::*;
        for (key, read) in iter {
            // We use fetch_data even with BlockSTMv2, because we don't want to record reads.
            if !match data_map.fetch_data_no_record(key, idx_to_validate) {
                Ok(Versioned(version, value)) => {
                    matches!(
                        self.data_read_comparator.compare_data_reads(
                            &DataRead::from_value_with_layout(version, value),
                            read
                        ),
                        DataReadComparison::Contains
                    )
                },
                // Dependency implies a validation failure.
                Err(Dependency(_)) | Err(Uninitialized) => false,
            } {
                return false;
            }
        }
        true
```

**File:** aptos-move/framework/aptos-framework/sources/aggregator/optional_aggregator.move (L92-106)
```text
    public fun switch(optional_aggregator: &mut OptionalAggregator) {
        if (optional_aggregator.aggregator.is_some()) {
            let aggregator = optional_aggregator.aggregator.extract();
            let limit = aggregator::limit(&aggregator);
            let value = aggregator::read(&aggregator);
            aggregator::destroy(aggregator);
            optional_aggregator.integer.fill(Integer {
                value, limit
            });
            return;
        }

        // Do not allow upgrades to parallelizable.
        abort error::invalid_state(ESWITCH_DEPRECATED)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aggregator/optional_aggregator.move (L136-155)
```text
    public fun add(optional_aggregator: &mut OptionalAggregator, value: u128) {
        if (optional_aggregator.aggregator.is_some()) {
            let aggregator = optional_aggregator.aggregator.borrow_mut();
            aggregator::add(aggregator, value);
        } else {
            let integer = optional_aggregator.integer.borrow_mut();
            add_integer(integer, value);
        }
    }

    /// Subtracts `value` from optional aggregator, aborting on going below zero.
    public fun sub(optional_aggregator: &mut OptionalAggregator, value: u128) {
        if (optional_aggregator.aggregator.is_some()) {
            let aggregator = optional_aggregator.aggregator.borrow_mut();
            aggregator::sub(aggregator, value);
        } else {
            let integer = optional_aggregator.integer.borrow_mut();
            sub_integer(integer, value);
        }
    }
```

**File:** aptos-move/framework/natives/src/aggregator_natives/context.rs (L182-198)
```rust
        // An aggregator whose value is known when created in this transaction
        // or read in this transaction. In this case, it is treated as a write.
        // Non-writes are written with legacy (no) metadata and never charged,
        // so it is important to differentiate here.
        let is_write = |id: &AggregatorID| -> bool {
            new_aggregators.contains(id) || read_aggregators.contains(id)
        };

        // Optimization disabled: the value is a concrete u128 tracked in place.
        for (id, value) in values {
            let change = if is_write(&id) {
                AggregatorChangeV1::Write(value)
            } else {
                AggregatorChangeV1::MaterializedDelta(value)
            };
            aggregator_v1_changes.insert(id.0, change);
        }
```

**File:** aptos-move/framework/natives/src/aggregator_natives/context.rs (L211-213)
```rust
        for id in destroyed_aggregators {
            aggregator_v1_changes.insert(id.0, AggregatorChangeV1::Delete);
        }
```
