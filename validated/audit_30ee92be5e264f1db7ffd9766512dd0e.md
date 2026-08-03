No vulnerability found for this question.

**Rationale:**

`get_write_summary` / `ReadWriteSummary` is not part of Block-STM's correctness-critical conflict-detection path. It is only used by `BlockGasLimitProcessor::accumulate_fee_statement` to compute a **gas conflict-penalty multiplier** for the block gas limit heuristic [1](#0-0) , which affects gas accounting/early-halt decisions, not actual read/write validation or commit correctness.

The real dependency tracking and re-validation logic operates on `MVHashMap`'s `versioned_cache` (`data()` and `group_data()`), which is populated directly from the transaction's full `resource_write_set()` and `resource_group_write_set()` (including every inner-op tag), independent of `get_write_summary` [2](#0-1) . Group writes are applied via `group_data().write_v2(...)` with the complete `group_ops` map, so a missing subkey in `get_write_summary`'s `HashSet<InputOutputKey>` cannot cause the versioned group data structure to lose that subkey or hide a dependency from validation.

Additionally, in production, `get_write_summary` is not attacker-controllable output — it's a fixed implementation on the real `AptosExecutorTask`/`BeforeMaterializationGuard`, derived deterministically from the same `resource_write_set()` iterated over in `guard.resource_write_set()`, and it correctly inserts one `InputOutputKey::Group` entry per tag in `write.inner_ops().keys()` [3](#0-2) . There is no unprivileged transaction input path that can make the real implementation diverge from the actual `resource_group_write_set()` inner ops; the "MockOutput" scenario described in the proof idea only exists in test harnesses (`combinatorial_tests/mock_executor.rs`), not in the production VM-output path.

Since (a) the summary is only a gas-heuristic input and (b) actual conflict/re-execution decisions come from the independently-populated `MVHashMap` (not from `get_write_summary`), an incomplete `get_write_summary` cannot corrupt a committed write set, cause stale/uncommitted reads, or produce non-deterministic committed state across replicas. This does not meet the state-integrity gate.

### Citations

**File:** aptos-move/block-executor/src/limit_processor.rs (L67-99)
```rust
    pub(crate) fn accumulate_fee_statement(
        &mut self,
        fee_statement: FeeStatement,
        txn_read_write_summary: Option<ReadWriteSummary<T>>,
        approx_output_size: Option<u64>,
    ) {
        self.accumulated_fee_statement
            .add_fee_statement(&fee_statement);
        self.txn_fee_statements.push(fee_statement);

        let conflict_multiplier = if let Some(conflict_overlap_length) =
            self.block_gas_limit_type.conflict_penalty_window()
        {
            let txn_read_write_summary = txn_read_write_summary.expect(
                "txn_read_write_summary needs to be computed if conflict_penalty_window is set",
            );
            if self.print_conflicts_info {
                println!("{:?}", txn_read_write_summary);
            }
            let rw_summary = if self
                .block_gas_limit_type
                .use_granular_resource_group_conflicts()
            {
                txn_read_write_summary
            } else {
                txn_read_write_summary.collapse_resource_group_conflicts()
            };
            self.txn_read_write_summaries.push(rw_summary);
            self.compute_conflict_multiplier(conflict_overlap_length as usize)
        } else {
            assert_none!(txn_read_write_summary);
            1
        };
```

**File:** aptos-move/block-executor/src/executor.rs (L252-342)
```rust
    fn process_resource_group_output_v2(
        maybe_output: Option<&E::Output>,
        idx_to_execute: TxnIndex,
        incarnation: Incarnation,
        last_input_output: &TxnLastInputOutput<T, E::Output>,
        versioned_cache: &MVHashMap<T::Key, T::Tag, ValueWithLayout<T::Value>, DelayedFieldID>,
        abort_manager: &mut AbortManager,
    ) -> Result<(), PanicError> {
        // The order of applying new group writes versus clearing previous writes is reversed
        // in BlockSTMv2 as opposed to V1, which avoids the necessity to clone group keys and
        // previous tags.

        let mut resource_group_write_set = maybe_output.map_or(Ok(HashMap::new()), |output| {
            output
                .before_materialization()
                .map(|inner| inner.resource_group_write_set())
        })?;

        last_input_output.for_each_resource_group_key_and_tags(
            idx_to_execute,
            |group_key_ref, prev_tags| {
                match resource_group_write_set.remove_entry(group_key_ref) {
                    Some((group_key, (group_metadata_op, group_size, group_ops))) => {
                        // Current incarnation overwrites the previous write to a group.
                        // TODO(BlockSTMv2): After MVHashMap refactoring, expose a single API
                        // for groups handling everything (inner resources, metadata & size).
                        abort_manager.invalidate_dependencies(
                            // Invalidate the readers of group metadata.
                            versioned_cache.data().write_v2::<true>(
                                group_key.clone(),
                                idx_to_execute,
                                incarnation,
                                group_metadata_op,
                            )?,
                        )?;
                        abort_manager.invalidate_dependencies(
                            versioned_cache.group_data().write_v2(
                                group_key,
                                idx_to_execute,
                                incarnation,
                                group_ops.into_iter(),
                                group_size,
                                prev_tags,
                            )?,
                        )?;
                    },
                    None => {
                        // Clean up the write from previous incarnation.
                        abort_manager.invalidate_dependencies(
                            // Invalidate the readers of group metadata.
                            versioned_cache
                                .data()
                                .remove_v2::<_, true>(group_key_ref, idx_to_execute)?,
                        )?;
                        abort_manager.invalidate_dependencies(
                            versioned_cache.group_data().remove_v2(
                                group_key_ref,
                                idx_to_execute,
                                prev_tags,
                            )?,
                        )?;
                    },
                }
                Ok(())
            },
        )?;

        // Handle any remaining entries in resource_group_write_set (new group writes)
        for (group_key, (group_metadata_op, group_size, group_ops)) in resource_group_write_set {
            // New group write that wasn't in previous incarnation
            abort_manager.invalidate_dependencies(
                // Invalidate the readers of group metadata.
                versioned_cache.data().write_v2::<true>(
                    group_key.clone(),
                    idx_to_execute,
                    incarnation,
                    group_metadata_op,
                )?,
            )?;
            abort_manager.invalidate_dependencies(versioned_cache.group_data().write_v2(
                group_key,
                idx_to_execute,
                incarnation,
                group_ops.into_iter(),
                group_size,
                HashSet::new(), // No previous tags since this is a new group write
            )?)?;
        }

        Ok(())
    }
```

**File:** aptos-move/aptos-vm/src/block_executor/mod.rs (L107-128)
```rust
    fn get_write_summary(&self) -> HashSet<InputOutputKey<StateKey, StructTag>> {
        let mut writes = HashSet::new();

        for (state_key, write) in self.guard.resource_write_set() {
            match write {
                AbstractResourceWriteOp::Write(..)
                | AbstractResourceWriteOp::WriteWithDelayedFields(_) => {
                    writes.insert(InputOutputKey::Resource(state_key.clone()));
                },
                AbstractResourceWriteOp::WriteResourceGroup(write) => {
                    for tag in write.inner_ops().keys() {
                        writes.insert(InputOutputKey::Group(state_key.clone(), tag.clone()));
                    }
                },
                AbstractResourceWriteOp::InPlaceDelayedFieldChange(_)
                | AbstractResourceWriteOp::ResourceGroupInPlaceDelayedFieldChange(_) => {
                    // No conflicts on resources from in-place delayed field changes.
                    // Delayed fields conflicts themselves are handled via
                    // delayed_field_change_set below.
                },
            }
        }
```
