[1](#0-0) [2](#0-1)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L1-1)
```rust
//! Vote state, vote program
```

**File:** programs/vote/src/vote_state/handler.rs (L710-717)
```rust
        );
        transaction_context
            .configure_top_level_instruction_for_tests(
                0,
                vec![InstructionAccount::new(1, false, true)],
                vec![],
            )
            .unwrap();
```
