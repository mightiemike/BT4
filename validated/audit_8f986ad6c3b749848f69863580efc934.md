[1](#0-0) [2](#0-1)

### Citations

**File:** runtime/runtime/src/function_call.rs (L164-171)
```rust
                    enqueue_promise_yield_timeout(
                        state_update,
                        &mut promise_yield_indices,
                        account_id.clone(),
                        receipt.input_data_ids[0],
                        apply_state.block_height
                            + config.wasm_config.limit_config.yield_timeout_length_in_blocks,
                    );
```

**File:** runtime/runtime/src/lib.rs (L2974-2977)
```rust
        // Queue entries are ordered by expires_at
        if queue_entry.expires_at > apply_state.block_height {
            break;
        }
```
