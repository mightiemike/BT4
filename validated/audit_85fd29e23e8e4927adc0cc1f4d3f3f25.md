### Title
Unchecked `u64` subtraction of `accounts_data_len_from_duplicates` can underflow/panic during startup index generation - ([File: accounts-db/src/accounts_db.rs])

### Summary
In `AccountsDb::generate_index`, after computing per-slot totals during snapshot-restore index generation, the accumulated `accounts_data_len` is reduced by `accounts_data_len_from_duplicates` (the data length attributable to older/duplicate versions of accounts found across multiple slots) using a bare `-=` operator on `u64` values, with no bounds check, unlike the adjacent `capitalization` field which is defended with `checked_sub(...).expect(...)`.

### Finding Description
During `generate_index()`, each storage slot is scanned and `accum.accounts_data_len` is accumulated as the sum of `data_len` for every non-zero-lamport account encountered across *all* slots/versions [1](#0-0) . Later, duplicate pubkeys (accounts present in more than one slot) are visited via `visit_duplicate_pubkeys_during_startup`, and the resulting `accounts_data_len_from_duplicates` is computed to represent the "extra" data length that should be removed since only the latest version of each duplicated pubkey should count [2](#0-1) .

The reduction step then does:
```rust
total_accum.capitalization = total_accum
    .capitalization
    .checked_sub(capitalization_from_duplicates)
    .expect("capitalization cannot underflow");
total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
``` [3](#0-2) 

The `capitalization` field is explicitly guarded with `checked_sub().expect(...)` because the developers recognized that the "duplicates" total could, under some accounting edge case, exceed the running total — exactly the same structural risk described in the external report where `dv_use` (the amount subtracted) can exceed `prev_value`. However, `accounts_data_len` receives no equivalent protection: it is decremented with a raw `-=`, which is checked-arithmetic in debug builds (panics on underflow) and silently wraps around in release builds (produces a bogus, enormous `u64` value) if `accounts_data_len_from_duplicates` is ever computed to be larger than `total_accum.accounts_data_len`. This is the closest reachable agave analog to the reported Vyper vault bug: a value derived from a "change/delta" that is implicitly assumed non-negative relative to a running total, then subtracted without a bounds check before being used as an unsigned quantity.

This code runs during `generate_index`, i.e., accounts-index/lattice-hash rebuilding during snapshot loading at startup [4](#0-3) , which is in the permitted "snapshot generation and rebuild" / "accounts or lattice hashing" scope.

### Impact Explanation
If the invariant `accounts_data_len_from_duplicates <= total_accum.accounts_data_len` is ever violated (e.g., due to a bug in the duplicate-visitation logic, a data-length calculation, or an unexpected combination of obsolete/zero-lamport account bookkeeping paths that were added later), the result is either:
- A validator panic in debug/test builds (denial-of-service at startup), or
- Silent wraparound to a near-`u64::MAX` value in release builds, corrupting `IndexGenerationInfo::accounts_data_len`, which feeds directly into the bank's on-chain `accounts_data_size` — potentially causing consensus-relevant divergence between honest nodes that computed the metric differently, or downstream panics/asserts when this corrupted size is later used for capacity/rent calculations.

### Likelihood Explanation
Likelihood is low: this requires `accounts_data_len_from_duplicates` (computed by `visit_duplicate_pubkeys_during_startup`, whose full internals I could not fully inspect in the available index) to exceed the sum accumulated during the initial per-slot scan. I could not confirm a concrete input/state that triggers this within the tool's indexing coverage; the only proof available is the structural absence of the same underflow protection that the neighboring `capitalization` field explicitly has, which strongly suggests the code owners recognized this class of risk exists for at least one of these two accumulators but did not apply the same fix everywhere.

### Recommendation
Apply the same `checked_sub` (or `saturating_sub` with a warning/metric) pattern used for `capitalization` to `total_accum.accounts_data_len -= accounts_data_len_from_duplicates`, e.g.:
```rust
total_accum.accounts_data_len = total_accum
    .accounts_data_len
    .checked_sub(accounts_data_len_from_duplicates)
    .expect("accounts_data_len cannot underflow");
```
so any pre-existing accounting inconsistency surfaces as a clear panic/log rather than a silent wraparound in release builds.

### Proof of Concept
Not independently reproduced — I was unable to fully trace `visit_duplicate_pubkeys_during_startup`'s computation of `accounts_data_len_from_duplicates` within the available codebase index to construct a concrete triggering snapshot/account-layout scenario. This is offered as a structural analog (unchecked subtraction of a "duplicate/delta" value from a running total, directly paralleling the reported Vyper bug pattern) rather than a confirmed exploitable path; a Devin session with full repository access would be needed to inspect `visit_duplicate_pubkeys_during_startup` and `populate_and_retrieve_duplicate_keys_from_startup` in full to confirm whether the invariant can actually be violated in practice.

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

**File:** accounts-db/src/accounts_db.rs (L6006-6025)
```rust
        let populate_duplicate_keys_us = measure_us!({
            // this has to happen before visit_duplicate_pubkeys_during_startup below
            // get duplicate keys from acct idx. We have to wait until we've finished flushing.
            self.accounts_index
                .populate_and_retrieve_duplicate_keys_from_startup(|slot_keys| {
                    total_duplicate_slot_keys.fetch_add(slot_keys.len() as u64, Ordering::Relaxed);
                    let unique_keys =
                        HashSet::<Pubkey>::from_iter(slot_keys.iter().map(|(_, key)| *key));
                    let unique_pubkeys_by_bin_inner = unique_keys.into_iter().collect::<Vec<_>>();
                    total_num_unique_duplicate_keys
                        .fetch_add(unique_pubkeys_by_bin_inner.len() as u64, Ordering::Relaxed);
                    // does not matter that this is not ordered by slot
                    unique_pubkeys_by_bin
                        .lock()
                        .unwrap()
                        .push(unique_pubkeys_by_bin_inner);
                });
        })
        .1;
        let unique_pubkeys_by_bin = unique_pubkeys_by_bin.into_inner().unwrap();
```

**File:** accounts-db/src/accounts_db.rs (L6042-6062)
```rust
        #[derive(Debug, Default)]
        struct DuplicatePubkeysVisitedInfo {
            accounts_data_len_from_duplicates: u64,
            num_duplicate_accounts: u64,
            duplicates_lt_hash: Box<DuplicatesLtHash>,
            capitalization_from_duplicates: u128,
        }
        impl DuplicatePubkeysVisitedInfo {
            fn reduce(mut self, other: Self) -> Self {
                self.accounts_data_len_from_duplicates += other.accounts_data_len_from_duplicates;
                self.num_duplicate_accounts += other.num_duplicate_accounts;
                self.duplicates_lt_hash
                    .0
                    .mix_in(&other.duplicates_lt_hash.0);
                self.capitalization_from_duplicates = self
                    .capitalization_from_duplicates
                    .checked_add(other.capitalization_from_duplicates)
                    .expect("capitalization cannot overflow");
                self
            }
        }
```

**File:** accounts-db/src/accounts_db.rs (L6107-6113)
```rust
        total_accum.lt_hash.mix_out(&duplicates_lt_hash.0);
        total_accum.capitalization = total_accum
            .capitalization
            .checked_sub(capitalization_from_duplicates)
            .expect("capitalization cannot underflow");
        total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
        info!("accounts data len: {}", total_accum.accounts_data_len);
```
