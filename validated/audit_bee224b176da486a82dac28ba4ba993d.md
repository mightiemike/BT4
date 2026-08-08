### Title
Unchecked subtraction on `accounts_data_len` during index generation can silently underflow/wrap - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::generate_index()` computes the total accounts data length by summing per-account data lengths across all storages, then subtracting the data length attributable to older (duplicate) versions of accounts. The capitalization subtraction right next to it is protected with `checked_sub().expect(...)`, but the `accounts_data_len` subtraction is a plain unchecked `-=` on a `u64`, mirroring the "division after subtraction/wrong-order arithmetic causing underflow" bug class from the external report (there, an unprotected subtraction/division ordering in `Pool.sol::getRedeemAmount` could underflow when `assetSupply` exceeded `tvl * PRECISION`).

### Finding Description
In `AccountsDb::generate_index()`: [1](#0-0) 

```rust
total_accum.lt_hash.mix_out(&duplicates_lt_hash.0);
total_accum.capitalization = total_accum
    .capitalization
    .checked_sub(capitalization_from_duplicates)
    .expect("capitalization cannot underflow");
total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
```

`total_accum.accounts_data_len` is accumulated in `generate_index_for_slot` by summing `data.len()` for every non-zero-lamport account scanned across every storage/slot (all versions, not just the latest) [2](#0-1) [3](#0-2) .

`accounts_data_len_from_duplicates` is separately computed by `visit_duplicate_pubkeys_during_startup`, which walks the accounts index (populated after all storages are scanned) to sum the data length of the "older" (non-max-slot) versions of every pubkey that has more than one slot entry [4](#0-3) .

The invariant that `accounts_data_len_from_duplicates <= total_accum.accounts_data_len` depends on every duplicate slot entry found via `self.accounts_index.scan(...)` being an exact, complete, and consistently-counted subset of what was tallied while scanning storages. Any divergence between what was accumulated into `total_accum.accounts_data_len` (which happens via `storage.scan_accounts` and is affected by `num_obsolete_accounts_skipped`, geyser/obsolete-account handling) and what the index later reports as duplicate slot_list entries (via `populate_and_retrieve_duplicate_keys_from_startup` / `accounts_index.scan`) can invalidate the assumption, producing an underflow of the unchecked `u64` subtraction. This is architecturally the same failure pattern as the reported Pool.sol bug: an assumed ordering/relationship between two quantities that is not defensively checked, leading to a subtraction underflow.

Unlike the capitalization computation immediately above it (which the developers explicitly hardened with `checked_sub().expect(...)` after presumably learning capitalization math needs to be defensive), the `accounts_data_len` computation was left as a bare `-=`, which is an inconsistency suggesting this specific line was not given the same defensive treatment.

### Impact Explanation
`generate_index()` runs at validator startup (loading from a snapshot) and produces `IndexGenerationInfo.accounts_data_len`, which seeds the bank's tracked accounts data length used for enforcing account-data-size limits and rent/size bookkeeping. Because the Cargo workspace does not set `overflow-checks = true` in the release profile (no such setting was found), a release-mode agave validator binary will not panic on an unsigned-integer subtraction underflow — it will silently wrap around, producing a bogus (near-`u64::MAX`) `accounts_data_len`. This is a silent state-corruption bug at startup, matching the "silent balance change"/hash-divergence bucket: any node that experiences the underflow condition would derive a different total accounts data length than one that computed it correctly, which is precisely the type of honest-node discrepancy this scan is meant to catch. If instead debug-assertions/overflow-checks were enabled (as in some CI/test profiles), the same code path would panic and crash the validator during startup/snapshot restoration.

### Likelihood Explanation
This is lower likelihood than a directly reachable transaction-triggered bug because it requires a specific combination of duplicate-pubkey/obsolete-account bookkeeping to diverge from the "no more duplicate data-len than the total" invariant — a condition that would need particular snapshot/account layouts (e.g., interplay between obsolete-account skipping during the scan phase and how those same slots are (or aren't) later visited as "duplicates" via the index). I could not conclusively construct or rule out a concrete sequence producing the divergence with the tools available; the code inconsistency (protected `checked_sub` next to unprotected `-=`) is the strongest evidence that the underflow condition was anticipated by the developers for capitalization but was overlooked for `accounts_data_len`.

### Recommendation
Replace the bare `-=` with a checked/saturating subtraction and an explicit assertion, mirroring the capitalization handling immediately above it:
```rust
total_accum.accounts_data_len = total_accum
    .accounts_data_len
    .checked_sub(accounts_data_len_from_duplicates)
    .expect("accounts_data_len cannot underflow");
```
This converts a potential silent wraparound (in release builds) into a clear, diagnosable panic, consistent with how the neighboring capitalization computation is already protected, and makes any real invariant violation immediately visible instead of silently corrupting startup-time accounting.

### Proof of Concept
A deterministic runtime PoC was not constructed within the scope of this analysis — reproducing the underflow requires crafting a snapshot/storage state where an obsolete-account-skip and duplicate-pubkey index bookkeeping interplay causes `accounts_data_len_from_duplicates` to exceed `total_accum.accounts_data_len`, which was not verified as reachable with the tools available. The finding is based on direct code inspection showing:
1. The asymmetric hardening between the capitalization subtraction (`checked_sub().expect(...)`) and the accounts_data_len subtraction (plain `-=`) at [5](#0-4) .
2. The two quantities being computed via separate, non-atomic passes (`generate_index_for_slot` scan vs. `visit_duplicate_pubkeys_during_startup` index scan) whose consistency is not independently verified before the subtraction.

### Citations

**File:** accounts-db/src/accounts_db.rs (L5730-5742)
```rust
        let num_obsolete_accounts_skipped = storage
            .scan_accounts(reader, |offset, account| {
                let data_len = account.data.len();
                stored_size_alive += storage.accounts.calculate_stored_size(data_len);
                let is_account_zero_lamport = account.is_zero_lamport();
                if !is_account_zero_lamport {
                    accounts_data_len += data_len as u64;
                    all_accounts_are_zero_lamports = false;
                } else {
                    // All zero lamport accounts are obsolete or single ref by the end of index
                    // generation. Store the offsets so they can be batch inserted later
                    zero_lamport_offsets.push(offset);
                }
```

**File:** accounts-db/src/accounts_db.rs (L5823-5829)
```rust
        accum.num_accounts += insert_info.count as u64;
        accum.insert_time_us += insert_time_us;
        accum.accounts_data_len += accounts_data_len;
        accum.num_did_not_exist += insert_info.num_did_not_exist;
        accum.num_existed_in_mem += insert_info.num_existed_in_mem;
        accum.num_existed_on_disk += insert_info.num_existed_on_disk;
        accum.num_obsolete_accounts_skipped += num_obsolete_accounts_skipped;
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

**File:** accounts-db/src/accounts_db.rs (L6230-6274)
```rust
    fn visit_duplicate_pubkeys_during_startup(
        &self,
        pubkeys: &[Pubkey],
    ) -> (u64, u64, Box<DuplicatesLtHash>, u128) {
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
```
