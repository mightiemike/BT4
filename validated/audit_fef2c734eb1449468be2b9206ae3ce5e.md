[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-binary-format/src/internals.rs (L11-16)
```rust
/// Represents a module index.
pub trait ModuleIndex {
    const KIND: IndexKind;

    fn into_index(self) -> usize;
}
```

**File:** third_party/move/move-binary-format/src/check_bounds.rs (L6-22)
```rust
use crate::{
    binary_views::BinaryIndexedView,
    errors::{
        bounds_error, offset_out_of_bounds as offset_out_of_bounds_error, verification_error,
        PartialVMError, PartialVMResult,
    },
    file_format::{
        Bytecode, CodeOffset, CodeUnit, CompiledModule, CompiledScript, Constant, FieldDefinition,
        FieldHandle, FieldInstantiation, FunctionDefinition, FunctionDefinitionIndex,
        FunctionHandle, FunctionInstantiation, LocalIndex, ModuleHandle, Signature, SignatureIndex,
        SignatureToken, StructDefInstantiation, StructDefinition, StructFieldInformation,
        StructHandle, StructVariantHandle, StructVariantInstantiation, TableIndex,
        TypeParameterIndex, VariantFieldHandle, VariantFieldInstantiation, VariantIndex,
    },
    internals::ModuleIndex,
    IndexKind,
};
```
