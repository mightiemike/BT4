[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/aptos-aggregator/src/bounded_math.rs (L50-56)
```rust
    pub fn unsigned_add(&self, base: u128, value: u128) -> BoundedMathResult<u128> {
        if self.max_value < base || value > (self.max_value - base) {
            Err(BoundedMathError::Overflow)
        } else {
            Ok(base + value)
        }
    }
```

**File:** aptos-move/aptos-aggregator/src/delta_math.rs (L199-246)
```rust
    fn offset_and_merge_min_overflow(
        min_overflow: &Option<u128>,
        prev_delta: &SignedU128,
        prev_min_overflow: &Option<u128>,
        math: &BoundedMath,
    ) -> Result<Option<u128>, DelayedFieldsSpeculativeError> {
        let adjusted_min_overflow = min_overflow.map_or(
            Ok(None),
            // Return Result<Option<u128>>. we want to have None on overflow,
            // and to fail the merging on underflow
            |min_overflow| {
                ok_overflow(math.unsigned_add_delta(min_overflow, prev_delta)).map_err(|_| {
                    DelayedFieldsSpeculativeError::DeltaHistoryMergeOffset {
                        target: min_overflow,
                        delta: *prev_delta,
                        max_value: math.get_max_value(),
                        reason:
                            DeltaHistoryMergeOffsetFailureReason::FailureNotExceedingBoundsAnyMore,
                    }
                })
            },
        )?;

        Ok(match (adjusted_min_overflow, prev_min_overflow) {
            (Some(a), Some(b)) => Some(u128::min(a, *b)),
            (a, b) => a.or(*b),
        })
    }

    fn offset_and_merge_max_achieved(
        max_achieved: u128,
        prev_delta: &SignedU128,
        prev_max_achieved: u128,
        math: &BoundedMath,
    ) -> Result<u128, DelayedFieldsSpeculativeError> {
        Ok(
            ok_underflow(math.unsigned_add_delta(max_achieved, prev_delta))
                .map_err(|_| DelayedFieldsSpeculativeError::DeltaHistoryMergeOffset {
                    target: max_achieved,
                    delta: *prev_delta,
                    max_value: math.get_max_value(),
                    reason: DeltaHistoryMergeOffsetFailureReason::AchievedExceedsBounds,
                })?
                .map_or(prev_max_achieved, |value| {
                    u128::max(prev_max_achieved, value)
                }),
        )
    }
```
