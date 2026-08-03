[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** third_party/move/move-vm/runtime/src/runtime_type_checks.rs (L566-583)
```rust
            Instruction::ImmBorrowFieldGeneric(idx) => {
                let struct_ty = operand_stack.pop_ty()?;
                let ((field_ty, _), (expected_struct_ty, _)) =
                    ty_cache.get_field_type_and_struct_type(*idx, frame)?;
                struct_ty.paranoid_check_ref_eq(expected_struct_ty, false)?;

                let field_ref_ty = ty_builder.create_ref_ty(field_ty, false)?;
                operand_stack.push_ty(field_ref_ty)?;
            },
            Instruction::MutBorrowFieldGeneric(idx) => {
                let struct_ty = operand_stack.pop_ty()?;
                let ((field_ty, _), (expected_struct_ty, _)) =
                    ty_cache.get_field_type_and_struct_type(*idx, frame)?;
                struct_ty.paranoid_check_ref_eq(expected_struct_ty, true)?;

                let field_mut_ref_ty = ty_builder.create_ref_ty(field_ty, true)?;
                operand_stack.push_ty(field_mut_ref_ty)?;
            },
```

**File:** third_party/move/move-vm/runtime/src/runtime_type_checks.rs (L718-722)
```rust
            Instruction::WriteRef => {
                let mut_ref_ty = operand_stack.pop_ty()?;
                let val_ty = operand_stack.pop_ty()?;
                mut_ref_ty.paranoid_write_ref(&val_ty)?;
            },
```

**File:** third_party/move/move-vm/runtime/src/runtime_type_checks.rs (L894-898)
```rust
            Instruction::FreezeRef => {
                let mut_ref_ty = operand_stack.pop_ty()?;
                let ref_ty = mut_ref_ty.paranoid_freeze_ref_ty()?;
                operand_stack.push_ty(ref_ty)?;
            },
```
