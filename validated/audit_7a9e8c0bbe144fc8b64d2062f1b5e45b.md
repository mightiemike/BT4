[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/mvhashmap/src/versioned_group_data.rs (L148-182)
```rust
    pub fn set_raw_base_values(
        &self,
        group_key: K,
        base_values: Vec<(T, V)>,
    ) -> anyhow::Result<()> {
        let mut group_sizes = self.group_sizes.entry(group_key.clone()).or_default();

        // Currently the size & value are written while holding the sizes lock.
        if let Vacant(entry) = group_sizes.size_entries.entry(ShiftedTxnIndex::zero_idx()) {
            // Perform group size computation if base not already provided.
            let group_size = group_size_as_sum::<T>(
                base_values
                    .iter()
                    .flat_map(|(tag, value)| value.bytes_len().map(|l| (tag.clone(), l))),
            )
            .map_err(|e| {
                anyhow!(
                    "Tag serialization error in resource group at {:?}: {:?}",
                    group_key.clone(),
                    e
                )
            })?;

            entry.insert(SizeEntry::new(SizeAndDependencies::from_size(group_size)));

            let mut superset_tags = self.group_tags.entry(group_key.clone()).or_default();
            for (tag, value) in base_values.into_iter() {
                superset_tags.insert(tag.clone());
                self.values
                    .set_base_value((group_key.clone(), tag), value, |_, _| {});
            }
        }

        Ok(())
    }
```

**File:** aptos-move/mvhashmap/src/versioned_group_data.rs (L269-278)
```rust
        let mut group_sizes = self.group_sizes.get_mut(&group_key).ok_or_else(|| {
            // Currently, we rely on read-before-write to make sure the group would have
            // been initialized, which would have created an entry in group_sizes. Group
            // being initialized sets up data-structures, such as superset_tags, which
            // is used in write_v2, hence the code invariant error. Note that in read API
            // (fetch_tagged_data) we return Uninitialized / TagNotFound errors, because
            // currently that is a part of expected initialization flow.
            // TODO(BlockSTMv2): when we refactor MVHashMap and group initialization logic,
            // also revisit and address the read-before-write assumption.
            code_invariant_error("Group (sizes) must be initialized to write to")
```

**File:** aptos-move/mvhashmap/src/versioned_group_data.rs (L531-547)
```rust
        let committed_group = superset_tags
            .into_iter()
            .map(
                |tag| match self.fetch_tagged_data_no_record(group_key, &tag, txn_idx + 1) {
                    Ok((_, value)) => Ok((value.write_op_kind() != WriteOpKind::Deletion)
                        .then(|| (tag, value.clone()))),
                    Err(MVGroupError::TagNotFound) => Ok(None),
                    Err(e) => Err(code_invariant_error(format!(
                        "Unexpected error in finalize group fetching value {:?}",
                        e
                    ))),
                },
            )
            .collect::<Result<Vec<_>, PanicError>>()?
            .into_iter()
            .flatten()
            .collect();
```
