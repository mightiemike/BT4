No vulnerability found for this question.

**Analysis:** The computation in question, `override_limit.min(onchain_limit)` at [1](#0-0) , uses Rust's standard `u64::min()`, which is a pure comparison operation — it returns whichever of the two values is smaller and performs no arithmetic (no addition, subtraction, or multiplication) that could overflow or saturate. There is no "u64 min edge case" in `Ord::min` for integers: for any two `u64` values, `a.min(b)` is fully deterministic and portable across all Rust builds/architectures, since integer comparison semantics are part of the language specification and not platform-dependent (unlike floating-point NaN handling, which is the only case where `.min()` has known non-associative/non-deterministic behavior).

The code's own comment confirms the intended semantics — the override is clamped so it can only lower, never raise, the effective cap [2](#0-1) . This is also directly covered by existing unit tests exercising exactly the extreme case described in the exploit question (`override_limit = u64::MAX`), confirming clamped behavior is correct and consistent: [3](#0-2) .

Since `min()` over two `u64` values cannot overflow, saturate differently, or diverge between validator builds, there is no mechanism by which a crafted `ComplexLimitV1` config plus an attacker-controlled override could cause `block_gas_limit()` to compute differently on different validators. The comparison is monotonic and identical by construction — no property-based counterexample exists for integer `min`. This claim does not correspond to a real code defect.

### Citations

**File:** aptos-move/block-executor/src/limit_processor.rs (L141-153)
```rust
    fn block_gas_limit(&self) -> Option<u64> {
        // The override is proposer-supplied (via the payload's TxnAndGasLimits) and
        // is not validated against the on-chain cap during consensus payload
        // verification. Clamp it to the on-chain limit so a Byzantine proposer
        // cannot raise the per-block gas cap; the override may only lower it.
        match (
            self.block_gas_limit_override,
            self.block_gas_limit_type.block_gas_limit(),
        ) {
            (Some(override_limit), Some(onchain_limit)) => Some(override_limit.min(onchain_limit)),
            (Some(override_limit), None) => Some(override_limit),
            (None, onchain_limit) => onchain_limit,
        }
```

**File:** aptos-move/block-executor/src/limit_processor.rs (L410-433)
```rust
    #[test]
    fn test_override_cannot_exceed_onchain_limit() {
        // Onchain effective cap is 100. A (potentially Byzantine) proposer-supplied
        // override of u64::MAX must be clamped to 100, not honored as-is.
        let block_gas_limit = BlockGasLimitType::ComplexLimitV1 {
            effective_block_gas_limit: 100,
            execution_gas_effective_multiplier: 1,
            io_gas_effective_multiplier: 1,
            conflict_penalty_window: 1,
            use_module_publishing_block_conflict: false,
            block_output_limit: None,
            include_user_txn_size_in_block_output: true,
            add_block_limit_outcome_onchain: false,
            use_granular_resource_group_conflicts: false,
        };

        let mut processor = TestProcessor::new(block_gas_limit, Some(u64::MAX), 10);

        processor.accumulate_fee_statement(execution_fee(60), None, None);
        assert!(!processor.should_end_block_parallel());
        // After 110 raw gas, the clamped (=100) onchain cap must trigger early halt.
        processor.accumulate_fee_statement(execution_fee(50), None, None);
        assert!(processor.should_end_block_parallel());
    }
```
