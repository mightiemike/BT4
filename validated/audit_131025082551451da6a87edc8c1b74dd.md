[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/aptos-vm/src/move_vm_ext/write_op_converter.rs (L201-244)
```rust
    fn convert(
        &self,
        state_value_metadata: Option<StateValueMetadata>,
        move_storage_op: MoveStorageOp<Bytes>,
        legacy_creation_as_modification: bool,
    ) -> PartialVMResult<WriteOp> {
        use MoveStorageOp::*;
        let write_op = match (state_value_metadata, move_storage_op) {
            (None, Modify(_) | Delete) => {
                // Possible under speculative execution, returning speculative error waiting for re-execution.
                return Err(
                    PartialVMError::new(StatusCode::SPECULATIVE_EXECUTION_ABORT_ERROR)
                        .with_message(
                            "When converting write op: updating non-existent value.".to_string(),
                        ),
                );
            },
            (Some(_), New(_)) => {
                // Possible under speculative execution, returning speculative error waiting for re-execution.
                return Err(
                    PartialVMError::new(StatusCode::SPECULATIVE_EXECUTION_ABORT_ERROR)
                        .with_message(
                            "When converting write op: Recreating existing value.".to_string(),
                        ),
                );
            },
            (None, New(data)) => match &self.new_slot_metadata {
                None => {
                    if legacy_creation_as_modification {
                        WriteOp::legacy_modification(data)
                    } else {
                        WriteOp::legacy_creation(data)
                    }
                },
                Some(metadata) => WriteOp::creation(data, metadata.clone()),
            },
            (Some(metadata), Modify(data)) => WriteOp::modification(data, metadata),
            (Some(metadata), Delete) => {
                // Inherit metadata even if the feature flags is turned off, for compatibility.
                WriteOp::deletion(metadata)
            },
        };
        Ok(write_op)
    }
```

**File:** aptos-move/aptos-vm-types/src/resource_group_adapter.rs (L324-375)
```rust
pub fn decrement_size_for_remove_tag(
    size: &mut ResourceGroupSize,
    old_tagged_resource_size: u64,
) -> PartialVMResult<()> {
    match size {
        ResourceGroupSize::Concrete(_) => Err(code_invariant_error(format!(
            "Unexpected ResourceGroupSize::Concrete in decrement_size_for_add_tag \
	     (removing resource w. size = {old_tagged_resource_size})"
        ))
        .into()),
        ResourceGroupSize::Combined {
            num_tagged_resources,
            all_tagged_resources_size,
        } => {
            *num_tagged_resources = num_tagged_resources
                .checked_sub(1)
                .ok_or_else(group_size_arithmetics_error)?;
            *all_tagged_resources_size = all_tagged_resources_size
                .checked_sub(old_tagged_resource_size)
                .ok_or_else(group_size_arithmetics_error)?;
            Ok(())
        },
    }
}

// Updates a given ResourceGroupSize (an abstract representation allowing the computation
// of bcs serialized size) size, to reflect the state after adding a resource in a group
// with size new_tagged_resource_size.
pub fn increment_size_for_add_tag(
    size: &mut ResourceGroupSize,
    new_tagged_resource_size: u64,
) -> PartialVMResult<()> {
    match size {
        ResourceGroupSize::Concrete(_) => Err(code_invariant_error(format!(
            "Unexpected ResourceGroupSize::Concrete in increment_size_for_add_tag \
		     (adding resource w. size = {new_tagged_resource_size})"
        ))
        .into()),
        ResourceGroupSize::Combined {
            num_tagged_resources,
            all_tagged_resources_size,
        } => {
            *num_tagged_resources = num_tagged_resources
                .checked_add(1)
                .ok_or_else(group_size_arithmetics_error)?;
            *all_tagged_resources_size = all_tagged_resources_size
                .checked_add(new_tagged_resource_size)
                .ok_or_else(group_size_arithmetics_error)?;
            Ok(())
        },
    }
}
```
