[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/aptos-vm-types/src/output.rs (L199-205)
```rust
        // materialize delayed fields into events
        if patched_events.len() != self.events().len() {
            return Err(code_invariant_error(
                "Different number of events and patched events in the output.",
            ));
        }
        self.change_set.set_events(patched_events.into_iter());
```

**File:** aptos-move/block-executor/src/task.rs (L224-231)
```rust
    fn incorporate_materialized_txn_output(
        &mut self,
        patched_resource_write_set: Vec<(
            <Self::Txn as Transaction>::Key,
            <Self::Txn as Transaction>::Value,
        )>,
        patched_events: Vec<<Self::Txn as Transaction>::Event>,
    ) -> Result<(Self::CommittedOutput, Trace), PanicError>;
```

**File:** aptos-move/aptos-vm-types/src/tests/test_output.rs (L36-59)
```rust
#[test]
fn test_ok_output_equality() {
    let vm_output = build_vm_output(
        vec![
            mock_create_with_layout("0", 0, None),
            mock_modify_with_layout("2", 2, None),
        ],
        vec![mock_module_modify("1", 1)],
        vec![],
    );

    // Two ways to construct the transaction output, which must agree when there are no
    // delayed fields to materialize:
    //   1. `try_materialize_into_transaction_output` just converts the output.
    //   2. `into_transaction_output_with_materialized_write_set` merges materialized
    //      delayed-field writes and combined groups (none here).
    let txn_output_1 = assert_ok!(vm_output.clone().into_transaction_output());
    let txn_output_2 = assert_ok!(vm_output
        .clone()
        .into_transaction_output_with_materialized_write_set(vec![], vec![]));

    assert_eq_outputs(&vm_output, txn_output_1);
    assert_eq_outputs(&vm_output, txn_output_2);
}
```
