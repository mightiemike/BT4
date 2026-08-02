[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/mvhashmap/src/versioned_delayed_fields.rs (L340-359)
```rust
            // Consider the latest entry below the provided version.
            |(idx, entry)| match (entry.as_ref().deref(), self.read_estimate_deltas) {
                (Value(v, _), _) => Ok(VersionedRead::Value(v.clone())),
                (Apply(apply), _) | (Estimate(Bypass(apply)), true) => {
                    apply.get_apply_base_id_option().map_or_else(
                        || self.apply_aggregator_change_suffix(&mut iter, apply),
                        |apply_base| {
                            let (base_id, end_index) = match apply_base {
                                ApplyBase::Previous(id) => (id, *idx),
                                ApplyBase::Current(id) => (id, *idx + 1),
                            };

                            Ok(VersionedRead::DependentApply(
                                base_id,
                                end_index,
                                apply.clone(),
                            ))
                        },
                    )
                },
```

**File:** aptos-move/aptos-aggregator/src/delayed_change.rs (L69-86)
```rust
    pub fn apply_to_base(
        &self,
        base_value: DelayedFieldValue,
    ) -> Result<DelayedFieldValue, PanicOr<DelayedFieldsSpeculativeError>> {
        use DelayedApplyChange::*;

        Ok(match self {
            AggregatorDelta { delta } => {
                DelayedFieldValue::Aggregator(delta.apply_to(base_value.into_aggregator_value()?)?)
            },
            SnapshotDelta { delta, .. } => {
                DelayedFieldValue::Snapshot(delta.apply_to(base_value.into_aggregator_value()?)?)
            },
            SnapshotDerived { formula, .. } => {
                DelayedFieldValue::Derived(formula.apply_to(base_value.into_snapshot_value()?))
            },
        })
    }
```
