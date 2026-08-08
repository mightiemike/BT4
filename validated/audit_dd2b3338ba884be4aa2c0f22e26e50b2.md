### Title
`agave-ledger-tool verify --run-final-accounts-hash-calculation` races with `AccountsBackgroundService`, allowing accounts state to be mutated after the lt-hash "request" is committed - ([File: ledger-tool/src/main.rs])

### Summary
The external report's bug class is: once a "randomness request" (an operation whose correctness depends on a frozen snapshot of inputs) has been submitted, no further mutation of those inputs should be allowed before the outcome is computed/consumed. The `verify` subcommand of `agave-ledger-tool` exposes exactly this pattern: `--run-final-accounts-hash-calculation` triggers `Bank::run_final_hash_calc()`, which flushes the cache and recomputes the accounts lattice hash from the index in the foreground, while the `AccountsBackgroundService` (ABS) may still be running concurrently and mutating the very index/storage state that the hash calculation reads.

### Finding Description
`Bank::run_final_hash_calc()` is explicitly documented as unsafe under concurrency: [1](#0-0) 

It calls `force_flush_accounts_cache()` and then `verify_accounts(None)`, which falls through to `AccountsDb::calculate_accounts_lt_hash_at_startup_from_index`: [2](#0-1) 

That function's own doc comment states it is "NOT safe to call concurrently with flush operations": [3](#0-2) 

The CLI flag that triggers this path documents the exact race and the fact that it can assert/panic: [4](#0-3) 

Crucially, in the `verify` subcommand's implementation, `load_and_process_ledger_or_exit` starts `AccountsBackgroundService` (which runs `flush_accounts_cache`, `clean_accounts`, and `shrink_candidate_slots` on a loop, per `AccountsBackgroundService::new`), but the `verify` code path never calls `accounts_background_service.join()` before (or after) doing print/hash operations on `working_bank`: [5](#0-4) 

Contrast this with the `create-snapshot` subcommand, which explicitly joins ABS first specifically to avoid this race: [6](#0-5) 

This is a direct structural analog to the reported bug class: the accounts-lt-hash "randomness request" (foreground recompute from the index) is issued, but the background "user-supplied input" mutator (ABS's flush/clean/shrink) is not fenced off, so it can still mutate the index/storage concurrently, changing the state being hashed mid-calculation.

### Impact Explanation
`calculate_accounts_lt_hash_at_startup_from_index` iterates the accounts index bins and, for each pubkey, looks up storage location and loads the account to mix into the lt-hash accumulator. If ABS's clean/shrink threads concurrently reclaim index entries, swap/replace storages, or remove dead storages while this iteration is in-flight, the index and storage backing an account can disappear or be relocated mid-read. This can silently produce an incorrect calculated lt-hash (a checksum/hash divergence from the bank's real, canonical `accounts_lt_hash`), causing `verify_accounts`/`run_final_hash_calc` to report a false verification failure (or, in the retry logic paths in `retry_to_get_account_accessor`, hit the `ABSURD_CONSECUTIVE_FAILED_ITERATIONS` fallback) — or in the worst case hit an internal assertion/panic in accounts-db's storage map operations (`assert!(self.no_shrink_in_progress(), ...)`, `assert!(self.map.insert(...).is_none())`, etc., seen in `account_storage.rs`). This matches the "hash/capitalization divergence" and "node panic" impact classes.

### Likelihood Explanation
This is directly reachable by any operator running `agave-ledger-tool verify --run-final-accounts-hash-calculation` (or by extension, any future direct caller of `Bank::run_final_hash_calc` without stopping ABS first), which is an in-scope, unprivileged, config-triggerable code path (no crafted snapshot or malicious peer required — a normal ledger-tool user hits it just by passing the documented flag). The flag's own help text already flags the risk ("Final hash calculation could race with accounts background service tasks and assert"), confirming the maintainers are aware the race is real but the `verify` subcommand does not defend against it the way `create-snapshot` does.

### Recommendation
In the `verify` subcommand implementation (`ledger-tool/src/main.rs`), join/stop `AccountsBackgroundService` before invoking any code path that depends on `Bank::run_final_hash_calc()` / `Bank::verify_accounts()`, mirroring the pattern already used in `create-snapshot` (`accounts_background_service.join().unwrap()`), or otherwise gate `--run-final-accounts-hash-calculation` behind an explicit stop-and-join of ABS. Alternatively, harden `calculate_accounts_lt_hash_at_startup_from_index` to take an explicit lock/guard that prevents concurrent flush/clean/shrink for the duration of the scan, rather than relying on a doc comment.

### Proof of Concept
1. Run `agave-ledger-tool verify --run-final-accounts-hash-calculation` against a ledger with a long enough replay window that ABS reaches its `CLEAN_INTERVAL`/`SHRINK_INTERVAL` while the final hash calculation would be running (or on a large enough account set that `calculate_accounts_lt_hash_at_startup_from_index`'s multi-second index walk overlaps with ABS's periodic `flush_accounts_cache`/`clean_accounts`/`shrink_candidate_slots` cycle, per `AccountsBackgroundService::new`'s loop body at `runtime/src/accounts_background_service.rs:526-571`).
2. Because `ledger-tool`'s `verify` arm never calls `accounts_background_service.join()` (unlike `create-snapshot`), ABS keeps running in the background while `run_final_hash_calc()`/`verify_accounts()` walks the accounts index and dereferences storage locations.
3. Depending on timing, ABS's shrink/clean can reclaim or relocate the storage entries being read, causing `calculate_accounts_lt_hash_at_startup_from_index` to compute against a torn/partial view of the state, producing a checksum mismatch ("Verifying accounts failed: accounts lattice hashes do not match") or, per the documented risk, an assertion panic in accounts-db's storage-map invariants.

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

**File:** runtime/src/bank.rs (L5456-5465)
```rust
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
```

**File:** accounts-db/src/accounts_db.rs (L4642-4650)
```rust
    /// Calculates the accounts lt hash
    ///
    /// Only intended to be called at startup (or by tests).
    /// Only intended to be used while testing the experimental accumulator hash.
    /// NOT safe to call concurrently with flush operations
    pub fn calculate_accounts_lt_hash_at_startup_from_index(
        &self,
        ancestors: &Ancestors,
    ) -> AccountsLtHash {
```

**File:** ledger-tool/src/main.rs (L1170-1179)
```rust
                .arg(
                    Arg::with_name("run_final_hash_calc")
                        .long("run-final-accounts-hash-calculation")
                        .takes_value(false)
                        .help(
                            "After 'verify' completes, run a final accounts hash calculation. \
                             Final hash calculation could race with accounts background service \
                             tasks and assert.",
                        ),
                )
```

**File:** ledger-tool/src/main.rs (L1888-1940)
```rust
                    let LoadAndProcessLedgerOutput { bank_forks, .. } =
                        load_and_process_ledger_or_exit(
                            arg_matches,
                            &genesis_config,
                            Arc::new(blockstore),
                            process_options,
                            transaction_status_sender,
                        );

                    let working_bank = bank_forks.read().unwrap().working_bank();
                    if print_accounts_stats {
                        working_bank.print_accounts_stats();
                    }
                    if print_bank_hash {
                        let slot_bank_hash = SlotBankHash {
                            slot: working_bank.slot(),
                            hash: working_bank.hash().to_string(),
                        };
                        println!("{}", output_format.formatted_string(&slot_bank_hash));
                    }
                    if write_bank_file {
                        bank_hash_details::write_bank_hash_details_file(&working_bank)
                            .map_err(|err| {
                                warn!("Unable to write bank hash_details file: {err}");
                            })
                            .ok();
                    }

                    if let Some(mut slot_recorder_config) = slot_recorder_config {
                        // Drop transaction_status_sender to break transaction_recorder
                        // out of its' receive loop
                        let transaction_status_sender =
                            slot_recorder_config.transaction_status_sender.take();
                        drop(transaction_status_sender);
                        if let Some(transaction_recorder) =
                            slot_recorder_config.transaction_recorder
                        {
                            transaction_recorder.join().unwrap();
                        }

                        let slot_details = slot_recorder_config.slot_details.lock().unwrap();
                        let bank_hashes =
                            bank_hash_details::BankHashDetails::new(slot_details.to_vec());

                        // writing the json file ends up with a syscall for each number, comma, indentation etc.
                        // use BufWriter to speed things up
                        let writer = std::io::BufWriter::new(slot_recorder_config.file);
                        serde_json::to_writer_pretty(writer, &bank_hashes).unwrap();
                    }

                    exit_signal.store(true, Ordering::Relaxed);
                    system_monitor_service.join().unwrap();
                }
```

**File:** ledger-tool/src/main.rs (L2186-2190)
```rust
                    // Snapshot creation will implicitly perform AccountsDb
                    // flush and clean operations. These operations cannot be
                    // run concurrently, so ensure ABS is stopped to avoid that
                    // possibility.
                    accounts_background_service.join().unwrap();
```
