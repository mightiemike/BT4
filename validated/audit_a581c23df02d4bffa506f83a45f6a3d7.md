[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** third_party/move/move-binary-format/src/internals.rs (L11-16)
```rust
/// Represents a module index.
pub trait ModuleIndex {
    const KIND: IndexKind;

    fn into_index(self) -> usize;
}
```

**File:** third_party/move/move-binary-format/src/check_bounds.rs (L147-152)
```rust
    fn check_module_handles(&self) -> PartialVMResult<()> {
        for module_handle in self.view.module_handles() {
            self.check_module_handle(module_handle)?
        }
        Ok(())
    }
```

**File:** third_party/move/move-binary-format/src/check_bounds.rs (L222-225)
```rust
    fn check_module_handle(&self, module_handle: &ModuleHandle) -> PartialVMResult<()> {
        check_bounds_impl(self.view.address_identifiers(), module_handle.address)?;
        check_bounds_impl(self.view.identifiers(), module_handle.name)
    }
```
