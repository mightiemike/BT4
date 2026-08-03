[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/aptos-aggregator/src/delayed_field_extension.rs (L197-220)
```rust
                DelayedChange::Apply(DelayedApplyChange::SnapshotDelta {
                    base_aggregator: aggregator_id,
                    delta: *delta,
                })
            },
            None => DelayedChange::Apply(DelayedApplyChange::SnapshotDelta {
                base_aggregator: aggregator_id,
                delta: DeltaWithMax {
                    update: SignedU128::Positive(0),
                    max_value,
                },
            }),
            _ => {
                return Err(code_invariant_error(
                    "Tried to snapshot a non-aggregator delayed field",
                )
                .into())
            },
        };

        let snapshot_id = resolver.generate_delayed_field_id(width);
        self.delayed_fields.insert(snapshot_id, change);
        Ok(snapshot_id)
    }
```

**File:** aptos-move/aptos-aggregator/src/delayed_field_extension.rs (L288-310)
```rust
        let change = match snapshot {
            // If snapshot is in Create state, we don't need to depend on it, and can just take the value.
            Some(DelayedChange::Create(DelayedFieldValue::Snapshot(value))) => {
                DelayedChange::Create(DelayedFieldValue::Derived(formula.apply_to(*value)))
            },
            Some(DelayedChange::Apply(DelayedApplyChange::SnapshotDelta { .. })) | None => {
                DelayedChange::Apply(DelayedApplyChange::SnapshotDerived {
                    base_snapshot: snapshot_id,
                    formula,
                })
            },
            _ => {
                return Err(code_invariant_error(
                    "Tried to string_concat a non-snapshot delayed field",
                )
                .into())
            },
        };

        let new_id = resolver.generate_delayed_field_id(width);
        self.delayed_fields.insert(new_id, change);
        Ok(new_id)
    }
```

**File:** aptos-move/aptos-aggregator/src/delayed_change.rs (L216-232)
```rust
impl<I: Copy + Clone> DelayedApplyEntry<I> {
    pub fn get_apply_base_id_option(&self) -> Option<ApplyBase<I>> {
        use DelayedApplyEntry::*;

        match self {
            AggregatorDelta { .. } => None,
            SnapshotDelta {
                base_aggregator, ..
            } => Some(ApplyBase::Previous(*base_aggregator)),
            SnapshotDerived { base_snapshot, .. } => Some(ApplyBase::Current(*base_snapshot)),
        }
    }

    pub fn get_apply_base_id(&self, self_id: &I) -> ApplyBase<I> {
        self.get_apply_base_id_option()
            .unwrap_or(ApplyBase::Previous(*self_id))
    }
```
