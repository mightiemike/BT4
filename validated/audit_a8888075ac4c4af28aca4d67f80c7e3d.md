### Title
`run_final_hash_calc` silently discards the accounts lattice-hash verification result - (File: `runtime/src/bank.rs`)

### Summary
`Bank::verify_accounts()` computes a `#[must_use]` boolean (`is_ok`) that indicates whether the freshly calculated accounts lattice hash (`accounts_lt_hash`) matches the value stored in the bank, but its only production caller, `Bank::run_final_hash_calc()`, explicitly discards that boolean with `_ = self.verify_accounts(None);` instead of propagating or acting on a mismatch.

### Finding Description
`verify_accounts()` is marked `#[must_use]` and returns `true`/`false` depending on whether `check_lt_hash()` finds the calculated accounts lt hash equal to the bank's stored/expected value: [1](#0-0) 

The only caller in `bank.rs`, `run_final_hash_calc()`, explicitly suppresses the `#[must_use]` boolean with `_ = self.verify_accounts(None);`, meaning a lattice-hash mismatch (accounts state divergence from the recorded/expected value) produces only an `error!` log line inside `check_lt_hash` and is never turned into a returned error, a panic, or any other actionable signal to the caller: [2](#0-1) 

This mirrors the reported bug class exactly: a boolean “did the check pass” value is computed correctly, but the code path that consumes it does not act on a `false` result — the operation (finishing hash calculation / letting ledger-tool consider the ledger verified) proceeds as if everything succeeded.

### Impact Explanation
`run_final_hash_calc()` is documented as being used by ledger-tool "to run a final hash calculation once all ledger replay has completed," i.e., it is the terminal accounts-hash/lattice-hash consistency check after a full replay. Since its result is thrown away, an accounts-state divergence (silent balance/account corruption, a bug in lt-hash incremental updates, or an incompletely replayed ledger) that manifests as an lt-hash mismatch will not be surfaced as a verification failure at this point — the only trace is a log line, which can go unnoticed in automated/ci pipelines that check process exit status rather than log contents. This can mask a genuine hash/capitalization divergence between the recorded state and what was actually replayed, undermining the very purpose of this "final" verification step.

### Likelihood Explanation
This will trigger any time the ledger-tool/analysis code path that calls `run_final_hash_calc()` encounters a real accounts lt-hash mismatch (e.g., due to a bug in incremental lt-hash accounting, a corrupted account write, or a divergent replay) — the mismatch is a data-integrity fact, not something requiring an attacker; likelihood of the underlying mismatch occurring is separate from this bug, but whenever it does occur, this particular verification path will unconditionally fail to report it.

### Recommendation
Do not discard the `bool` returned by `verify_accounts()` in `run_final_hash_calc()`. Propagate a failure (e.g., return a `Result`, or `panic!`/`process::exit` with a clear message) so that callers (ledger-tool, tests) can detect and act on an accounts-state verification failure instead of silently continuing.

### Proof of Concept
Not applicable as a live exploit — this is a logic-omission bug that can be demonstrated statically: `verify_accounts` is `#[must_use]` yet its only call site suppresses the result via `_ = self.verify_accounts(None);`, so any code path relying on `run_final_hash_calc()` to detect a lattice-hash mismatch will not observe a failure signal, as shown at [2](#0-1)  and [1](#0-0) .

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
