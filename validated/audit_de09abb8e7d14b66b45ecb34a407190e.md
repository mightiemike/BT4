[1](#0-0)

### Citations

**File:** aptos-move/aptos-vm-types/src/change_set.rs (L101-150)
```rust
    pub fn new_expanded(
        resource_write_set: BTreeMap<StateKey, (WriteOp, Option<TriompheArc<MoveTypeLayout>>)>,
        resource_group_write_set: BTreeMap<StateKey, GroupWrite>,
        aggregator_v1_write_set: BTreeMap<StateKey, AbstractResourceWriteOp>,
        delayed_field_change_set: BTreeMap<DelayedFieldID, DelayedChange<DelayedFieldID>>,
        reads_needing_delayed_field_exchange: BTreeMap<
            StateKey,
            (StateValueMetadata, u64, TriompheArc<MoveTypeLayout>),
        >,
        group_reads_needing_delayed_field_exchange: BTreeMap<StateKey, (StateValueMetadata, u64)>,
        events: Vec<(ContractEvent, Option<MoveTypeLayout>)>,
    ) -> PartialVMResult<Self> {
        Ok(Self::new(
            resource_write_set
                .into_iter()
                .map::<PartialVMResult<_>, _>(|(k, (w, l))| {
                    Ok((
                        k,
                        AbstractResourceWriteOp::from_resource_write_with_maybe_layout(w, l),
                    ))
                })
                .chain(
                    resource_group_write_set
                        .into_iter()
                        .map(|(k, w)| Ok((k, AbstractResourceWriteOp::WriteResourceGroup(w)))),
                )
                .chain(reads_needing_delayed_field_exchange.into_iter().map(
                    |(k, (metadata, size, layout))| {
                        Ok((
                            k,
                            AbstractResourceWriteOp::InPlaceDelayedFieldChange(
                                InPlaceDelayedFieldChangeOp {
                                    layout,
                                    materialized_size: size,
                                    metadata,
                                    is_aggregator_v1_delta: false,
                                },
                            ),
                        ))
                    },
                ))
                .chain(group_reads_needing_delayed_field_exchange.into_iter().map(
                    |(k, (metadata, materialized_size))| {
                        Ok((
                            k,
                            AbstractResourceWriteOp::ResourceGroupInPlaceDelayedFieldChange(
                                ResourceGroupInPlaceDelayedFieldChangeOp {
                                    metadata,
                                    materialized_size,
                                },
```
