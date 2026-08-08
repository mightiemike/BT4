### Title
Unchecked subtraction of `accounts_data_len` during startup index generation can underflow/panic or silently corrupt accounts-data-size accounting - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::generate_index()` computes a running total of live account data length (`total_accum.accounts_data_len`) while scanning all storages, then subtracts the data length contributed by superseded ("duplicate") versions of pubkeys found across multiple slots. Unlike the adjacent capitalization adjustment, which uses `checked_sub().expect(...)`, the `accounts_data_len` adjustment uses a bare `-=` with no overflow protection.

### Finding Description
During index generation (run at startup when rebuilding `AccountsDb` from snapshot storages), each storage slot is scanned and `accounts_data_len` is accumulated for every non-zero-lamport account [1](#0-0) . Separately, `visit_duplicate_pubkeys_during_startup()` walks pubkeys that appear in more than one slot and sums the data length of all *older* (superseded) versions with `lamports > 0` into `accounts_data_len_from_duplicates` [2](#0-1) .

These two values are then combined:
```
total_accum.capitalization = total_accum
    .capitalization
    .checked_sub(capitalization_from_duplicates)
    .expect("capitalization cannot underflow");
total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
``` [3](#0-2) 

The capitalization subtraction is explicitly guarded with `checked_sub()` plus a descriptive panic message, but the immediately adjacent, logically parallel `accounts_data_len` subtraction is a plain, unchecked `u64 -= u64`. This is the same class of defect described in the external report: an arithmetic operation whose inputs are derived from independently-computed accumulators (analogous to `tokenDecimals`, `sizeDecimals`, `priceDecimals`) is not validated to ensure the subtrahend cannot exceed the minuend before the operation executes.

No repository-wide `overflow-checks` override was found in build configuration, so the specific failure mode depends on the build profile: in debug/test builds Rust will panic ("attempt to subtract with overflow") crashing index generation; in a release build compiled without overflow checks the subtraction wraps silently, producing a corrupted `total_accum.accounts_data_len`.

This value flows directly into `IndexGenerationInfo::accounts_data_len` [4](#0-3) , which is passed to `Bank::new_from_snapshot()` via `reconstruct_bank_from_fields()` [5](#0-4)  and seeds `accounts_data_size_initial`, the baseline used by `load_accounts_data_size()` for all subsequent accounts-data-size accounting [6](#0-5) .

### Impact Explanation
- If the subtraction panics, index generation aborts during startup, preventing the node from finishing snapshot load — a node-availability (panic/DoS-of-self) issue for any honest node whose snapshot naturally contains duplicate pubkey versions across slots (a normal, expected startup condition, not attacker-crafted).
- If it silently wraps (release build without overflow checks), `accounts_data_len` becomes a near-`u64::MAX` corrupted value. Since this seeds the bank's baseline accounts-data-size counter, it would cause `load_accounts_data_size()` to report an incorrect (wildly inflated) size, which can cause spurious `MaxAccountsDataAllocationsExceeded`/limit-exceeded failures for all subsequent transactions that grow account data on that node — a silent, node-local correctness divergence rather than a cluster-wide consensus break, but a real functional/availability defect on the affected validator.

### Likelihood Explanation
The code path executes on every startup that goes through `generate_index()` (all full/incremental snapshot loads and snapshot-dir restores), which is routine, honest-node operation — not dependent on a maliciously crafted snapshot. Duplicate pubkey versions across slots are an expected, common occurrence (the same account written to storage in multiple slots before compaction/clean removes older copies), so `accounts_data_len_from_duplicates` is computed and applied on essentially every real-world startup. The precondition for the underflow itself (i.e., an accounting inconsistency where duplicates' summed data length exceeds the running total) is not proven here to be reachable in the current logic — I could not find or construct a concrete input sequence that forces `accounts_data_len_from_duplicates > total_accum.accounts_data_len` from the available context, so the underflow trigger condition is not confirmed, only the lack of defensive validation relative to the adjacent, properly-guarded capitalization computation.

### Recommendation
Change `total_accum.accounts_data_len -= accounts_data_len_from_duplicates;` to use `checked_sub()` with an explicit `.expect("accounts data len cannot underflow")` (mirroring the capitalization guard immediately above it), or `saturating_sub()` if underflow is truly expected to be impossible and a hard panic is undesirable. This ensures any accounting inconsistency is either loudly caught with a clear message (matching the pattern already used for capitalization) or safely clamped rather than silently wrapping.

### Proof of Concept
Not established. I was unable to confirm from the available index/context whether `accounts_data_len_from_duplicates` can be produced larger than `total_accum.accounts_data_len` under any realistic snapshot/replay history; a concrete reproducing sequence of slots/duplicate pubkeys would need to be constructed and verified against the full `generate_index()`/`visit_duplicate_pubkeys_during_startup()` implementation (and any related "obsolete accounts" bookkeeping) to demonstrate an actual underflow trigger. This should be treated as a defense-in-depth / code-quality finding (missing validation analogous to the reported bug class) rather than a demonstrated exploitable underflow.

### Citations

**File:** accounts-db/src/accounts_db.rs (L5730-5737)
```rust
        let num_obsolete_accounts_skipped = storage
            .scan_accounts(reader, |offset, account| {
                let data_len = account.data.len();
                stored_size_alive += storage.accounts.calculate_stored_size(data_len);
                let is_account_zero_lamport = account.is_zero_lamport();
                if !is_account_zero_lamport {
                    accounts_data_len += data_len as u64;
                    all_accounts_are_zero_lamports = false;
```

**File:** accounts-db/src/accounts_db.rs (L6108-6113)
```rust
        total_accum.capitalization = total_accum
            .capitalization
            .checked_sub(capitalization_from_duplicates)
            .expect("capitalization cannot underflow");
        total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
        info!("accounts data len: {}", total_accum.accounts_data_len);
```

**File:** accounts-db/src/accounts_db.rs (L6179-6184)
```rust
        IndexGenerationInfo {
            accounts_data_len: total_accum.accounts_data_len,
            calculated_accounts_lt_hash: AccountsLtHash(total_accum.lt_hash),
            calculated_capitalization,
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L6234-6285)
```rust
        let mut accounts_data_len_from_duplicates = 0;
        let mut num_duplicate_accounts = 0_u64;
        let mut duplicates_lt_hash = Box::new(DuplicatesLtHash::default());
        let mut capitalization_from_duplicates = 0_u128;
        self.accounts_index.scan(
            pubkeys.iter(),
            |pubkey, slots_refs| {
                if let Some((slot_list, _ref_count)) = slots_refs
                    && slot_list.len() > 1
                {
                    // Only the account data len in the highest slot should be used, and the rest are
                    // duplicates.  So find the max slot to keep.
                    // Then sum up the remaining data len, which are the duplicates.
                    // All of the slots need to go in the 'uncleaned_slots' list. For clean to work properly,
                    // the slot where duplicate accounts are found in the index need to be in 'uncleaned_slots' list, too.
                    let max = slot_list.iter().map(|(slot, _)| slot).max().unwrap();
                    slot_list.iter().for_each(|(slot, account_info)| {
                        if slot == max {
                            // the info in 'max' is the most recent, current info for this pubkey
                            return;
                        }
                        let maybe_storage_entry = self
                            .storage
                            .get_account_storage_entry(*slot, account_info.store_id());
                        let mut accessor = LoadedAccountAccessor::Stored(
                            maybe_storage_entry.map(|entry| (entry, account_info.offset())),
                        );
                        accessor.check_and_get_loaded_account(|loaded_account| {
                            let data_len = loaded_account.data_len();
                            let lamports = loaded_account.lamports();
                            if lamports > 0 {
                                accounts_data_len_from_duplicates += data_len;
                            }
                            num_duplicate_accounts += 1;
                            let account_lt_hash = Self::lt_hash_account(&loaded_account, pubkey);
                            duplicates_lt_hash.0.mix_in(&account_lt_hash.0);
                            capitalization_from_duplicates = capitalization_from_duplicates
                                .checked_add(u128::from(lamports))
                                .expect("capitalization cannot overflow");
                        });
                    });
                }
            },
            ScanFilter::All,
        );
        (
            accounts_data_len_from_duplicates as u64,
            num_duplicate_accounts,
            duplicates_lt_hash,
            capitalization_from_duplicates,
        )
    }
```

**File:** runtime/src/serde_snapshot.rs (L1198-1215)
```rust
    info!("Building accounts index...");
    let start = Instant::now();
    let IndexGenerationInfo {
        accounts_data_len,
        calculated_accounts_lt_hash,
        calculated_capitalization,
    } = accounts_db.generate_index(limit_load_slot_count_from_snapshot, verify_index);
    info!("Building accounts index... Done in {:?}", start.elapsed());

    Ok((
        accounts_db,
        ReconstructedAccountsDbInfo {
            accounts_data_len,
            calculated_accounts_lt_hash,
            calculated_capitalization,
            bank_hash_stats: snapshot_bank_hash_info.stats,
        },
    ))
```

**File:** runtime/src/bank.rs (L4224-4228)
```rust
    /// Load the accounts data size, in bytes
    pub fn load_accounts_data_size(&self) -> u64 {
        self.accounts_data_size_initial
            .saturating_add_signed(self.load_accounts_data_size_delta())
    }
```
