[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-vm/runtime/src/frame_type_cache.rs (L42-49)
```rust
    /// For a given field instantiation, the:
    ///    ((Type of the field, size of the field type) and (Type of its defining struct,
    ///    size of its defining struct)
    field_instantiation:
        BTreeMap<FieldInstantiationIndex, ((Type, NumTypeNodes), (Type, NumTypeNodes))>,
    /// Same as above, but for variant field instantiations
    variant_field_instantiation:
        BTreeMap<VariantFieldInstantiationIndex, ((Type, NumTypeNodes), (Type, NumTypeNodes))>,
```

**File:** third_party/move/move-vm/runtime/src/frame_type_cache.rs (L186-206)
```rust
    // note(inline): do not inline, increases size a lot, might even decrease the performance
    pub(crate) fn get_struct_variant_fields_types(
        &mut self,
        idx: StructVariantInstantiationIndex,
        frame: &Frame,
    ) -> PartialVMResult<&[(Type, NumTypeNodes)]> {
        Ok(get_or_insert!(
            &mut self.struct_variant_field_type_instantiation,
            idx,
            {
                frame
                    .instantiate_generic_struct_variant_fields(idx)?
                    .into_iter()
                    .map(|ty| {
                        let num_nodes = NumTypeNodes::new(ty.num_nodes() as u64);
                        (ty, num_nodes)
                    })
                    .collect::<Vec<_>>()
            }
        ))
    }
```
