[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-bytecode-verifier/src/reference_safety/abstract_state.rs (L181-194)
```rust
    fn add_local_borrow(&mut self, local: LocalIndex, id: RefID) {
        self.borrow_graph
            .add_strong_field_borrow((), self.frame_root(), Label::Local(local), id)
    }

    fn add_resource_borrow(&mut self, resource: StructDefinitionIndex, id: RefID) {
        self.borrow_graph
            .add_weak_field_borrow((), self.frame_root(), Label::Global(resource), id)
    }

    /// removes `id` from borrow graph
    fn release(&mut self, id: RefID) {
        self.borrow_graph.release(id);
    }
```

**File:** third_party/move/move-bytecode-verifier/src/reference_safety/abstract_state.rs (L285-301)
```rust
    pub fn move_loc(
        &mut self,
        offset: CodeOffset,
        local: LocalIndex,
    ) -> PartialVMResult<AbstractValue> {
        let old_value = std::mem::replace(
            safe_unwrap!(self.locals.get_mut(local as usize)),
            AbstractValue::NonReference,
        );
        match old_value {
            AbstractValue::Reference(id) => Ok(AbstractValue::Reference(id)),
            AbstractValue::NonReference if self.is_local_borrowed(local) => {
                Err(self.error(StatusCode::MOVELOC_EXISTS_BORROW_ERROR, offset))
            },
            AbstractValue::NonReference => Ok(AbstractValue::NonReference),
        }
    }
```
