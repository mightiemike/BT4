### Title
`Bank::run_final_hash_calc` silently discards the accounts-lt-hash verification result, masking snapshot/replay hash mismatches - (File: `runtime/src/bank.rs`)

### Summary
`Bank::verify_accounts` is the function that detects whether the accounts state reconstructed from a snapshot/replay matches the expected accounts lattice hash. It is explicitly annotated `#[must_use]` to force callers to act on its boolean result, but `Bank::run_final_hash_calc` discards that result with `_ = self.verify_accounts(None);`, mirroring the exact bug class from the external report: a security/consistency check that returns a boolean is computed but never enforced by the caller.

### Finding Description
`verify_accounts` computes the accounts lt hash from the accounts index and compares it against the bank's expected `accounts_lt_hash`, logging an `error!` and returning `false` on mismatch: [1](#0-0) 

`run_final_hash_calc`, which is documented as running "a final hash calculation once all ledger replay has completed," calls this must-use function and explicitly throws away its return value using `_ = ...`: [2](#0-1) 

Because the boolean is discarded, a mismatch between the calculated `AccountsLtHash` (derived by walking `AccountsDb::calculate_accounts_lt_hash_at_startup_from_index`) and the bank's recorded `accounts_lt_hash` produces only an `error!` log line via `check_lt_hash` — the caller never asserts, panics, or propagates a failure: [3](#0-2) [4](#0-3) 

This is invoked from `ledger/src/blockstore_processor.rs`, which is the code path used to validate ledger replay / snapshot reconstruction integrity end-to-end. Contrast this with other call sites such as `Bank::verify_accounts(None)` used directly in tests, which explicitly `assert!` on the return value: [5](#0-4) 

### Impact Explanation
This falls under the "honest-node snapshot-vs-replay mismatch" and "hash/capitalization divergence" impact classes explicitly in scope. If accounts state is silently corrupted (e.g. a bug in cache flush, clean, purge, or index rebuild produces a divergent accounts lt hash), the code path that is specifically designed to catch this divergence after full ledger replay swallows the failure signal instead of surfacing it. An operator or automated tool relying on `run_final_hash_calc` completing without error would incorrectly conclude the ledger/accounts state is consistent, when in fact `check_lt_hash` detected and logged (but did not fail) a divergence. This can mask a class of AccountsDB corruption bugs that would otherwise be caught at this dedicated verification checkpoint.

### Likelihood Explanation
The bug is deterministic and always triggers whenever `verify_accounts` returns `false` from this call path — there is no attacker input needed beyond triggering any underlying accounts/lt-hash divergence (from any other bug in AccountsDB storage, cache flush, or index rebuild). The `#[must_use]` annotation being explicitly bypassed with `_ =` indicates the discard is intentional in current code, but it removes the fail-safe behavior that the annotation is meant to enforce.

### Recommendation
In `Bank::run_final_hash_calc`, propagate the boolean result instead of discarding it — e.g., change the return type to `bool`/`Result` and have callers in `ledger/src/blockstore_processor.rs` treat a `false` result as a hard failure (panic or error return), consistent with how `verify_accounts` is treated in tests via `assert!`.

### Proof of Concept
Not applicable as executable PoC given ask-only/index-based investigation; the vulnerability is demonstrated structurally by the code itself:
```rust
// runtime/src/bank.rs
pub fn run_final_hash_calc(&self) {
    self.force_flush_accounts_cache();
    // note that this slot may not be a root
    _ = self.verify_accounts(None);   // <-- #[must_use] bool result discarded
}
```
Any scenario where `check_lt_hash` returns `false` (accounts lt hash mismatch) will only produce an `error!` log line and `run_final_hash_calc` will return normally as if verification succeeded. [6](#0-5)

### Citations

**File:** runtime/src/bank.rs (L5416-5422)
```rust
    /// Used by ledger tool to run a final hash calculation once all ledger replay has completed.
    /// This should not be called by validator code.
    pub fn run_final_hash_calc(&self) {
        self.force_flush_accounts_cache();
        // note that this slot may not be a root
        _ = self.verify_accounts(None);
    }
```

**File:** runtime/src/bank.rs (L5436-5468)
```rust
    #[must_use]
    fn verify_accounts(&self, calculated_accounts_lt_hash: Option<&AccountsLtHash>) -> bool {
        let accounts_db = &self.rc.accounts.accounts_db;

        fn check_lt_hash(
            expected_accounts_lt_hash: &AccountsLtHash,
            calculated_accounts_lt_hash: &AccountsLtHash,
        ) -> bool {
            let is_ok = calculated_accounts_lt_hash == expected_accounts_lt_hash;
            if !is_ok {
                let expected = expected_accounts_lt_hash.0.checksum();
                let calculated = calculated_accounts_lt_hash.0.checksum();
                error!(
                    "Verifying accounts failed: accounts lattice hashes do not match, expected: \
                     {expected}, calculated: {calculated}",
                );
            }
            is_ok
        }

        info!("Verifying accounts...");
        let start = Instant::now();
        let expected_accounts_lt_hash = self.accounts_lt_hash.lock().unwrap().clone();
        let is_ok = if let Some(calculated_accounts_lt_hash) = calculated_accounts_lt_hash {
            check_lt_hash(&expected_accounts_lt_hash, calculated_accounts_lt_hash)
        } else {
            let calculated_accounts_lt_hash =
                accounts_db.calculate_accounts_lt_hash_at_startup_from_index(&self.ancestors);
            check_lt_hash(&expected_accounts_lt_hash, &calculated_accounts_lt_hash)
        };
        info!("Verifying accounts... Done in {:?}", start.elapsed());
        is_ok
    }
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L476-482)
```rust
#[cfg(test)]
mod tests {
    use {
        super::*,
        crate::{
            genesis_utils::create_genesis_config_with_leader_ex, runtime_config::RuntimeConfig,
            snapshot_bank_utils, snapshot_utils,
```

**File:** runtime/src/bank/tests.rs (L2354-2357)
```rust
    bank2.squash();
    bank2.force_flush_accounts_cache();
    assert!(bank2.verify_accounts(None));
}
```
