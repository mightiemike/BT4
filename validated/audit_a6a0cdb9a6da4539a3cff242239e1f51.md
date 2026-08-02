[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/block-executor/src/scheduler.rs (L230-244)
```rust
/// (1) Upon decreasing validation_idx, increment validation_idx.wave and update txn's
/// max_triggered_wave <- validation_idx.wave;
/// (2) Upon finishing execution of txn that is below validation_idx, update txn's
/// required_wave <- validation_idx.wave; (otherwise, the last triggered wave is below and will validate).
/// (3) Upon validating a txn successfully, update txn's maybe_max_validated_wave <- validation_idx.wave;
/// (4) Upon trying to commit an executed txn, update commit_state.wave <- txn's max_triggered_wave.
/// (5) If txn's maybe_max_validated_wave >= max(commit_state.wave, txn's required_wave), can commit the txn.
///
/// Remark: commit_state.wave is updated only with max_triggered_wave but not required_wave. This is
/// because max_triggered_wave implies that this wave of validations was required for all higher transactions
/// (and is set as a part of decrease_validation_idx), while required_wave is set for the transaction only
/// (when a validation task is returned to the caller). Moreover, the code is structured in a way that
/// decrease_validation_idx is always called for txn_idx + 1 (e.g. when aborting, there is no need to validate
/// the transaction before re-execution, and in finish_execution, even if there is a need to validate txn_idx,
/// it is returned to the caller directly, which is done so as an optimization and also for uniformity).
```

**File:** aptos-move/block-executor/src/scheduler.rs (L246-260)
```rust
struct ValidationStatus {
    max_triggered_wave: Wave,
    required_wave: Wave,
    maybe_max_validated_wave: Option<Wave>,
}

impl ValidationStatus {
    pub fn new() -> Self {
        ValidationStatus {
            max_triggered_wave: 0,
            required_wave: 0,
            maybe_max_validated_wave: None,
        }
    }
}
```
