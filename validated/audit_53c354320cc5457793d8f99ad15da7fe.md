[1](#0-0)

### Citations

**File:** execution/executor/src/workflow/do_ledger_update.rs (L35-48)
```rust
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );

        // Calculate root hash
        let transaction_accumulator = Arc::new(parent_accumulator.append(&transaction_info_hashes));
```
