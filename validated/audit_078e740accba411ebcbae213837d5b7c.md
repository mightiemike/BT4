### Title
Unchecked `u64` subtraction in `accounts_data_len` calculation during index generation - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::generate_index()` computes the accounts data length at startup by first summing the data length of every stored account (including all duplicate/older versions of the same pubkey across slots), then subtracting the portion contributed by duplicate accounts to arrive at the "true" total. Immediately adjacent to this subtraction, the equivalent lamports/capitalization calculation is protected with `checked_sub()` and an explicit `.expect("capitalization cannot underflow")`, but the `accounts_data_len` subtraction uses a bare `-=` operator with no overflow/underflow protection.

### Finding Description
In `generate_index()`, after computing `total_accum.accounts_data_len` (the raw sum of every account instance's data length across all slots) and `accounts_data_len_from_duplicates` (the sum of data lengths contributed by all-but-the-latest duplicate account versions), the code does: [1](#0-0) 

```
total_accum.lt_hash.mix_out(&duplicates_lt_hash.0);
total_accum.capitalization = total_accum
    .capitalization
    .checked_sub(capitalization_from_duplicates)
    .expect("capitalization cannot underflow");
total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
```

The `capitalization` field (a `u128`) is subtracted using `checked_sub()` with an explicit panic message guarding against underflow, following the exact SafeMath-style pattern the external report recommends. `accounts_data_len` (a plain `u64`) is decremented with the unchecked `-=` operator right next to it — the same inconsistency the external report calls out ("not all calculations use SafeMath"/checked arithmetic, while adjacent, structurally identical calculations do).

`accounts_data_len_from_duplicates` is derived in `visit_duplicate_pubkeys_during_startup()` by summing `data_len` for every non-latest slot entry of a duplicate pubkey with `lamports > 0`: [2](#0-1) 

and `total_accum.accounts_data_len` is accumulated per-slot in `generate_index_for_slot()`: [3](#0-2) [4](#0-3) 

Both quantities are computed from independent parallel scans (via `populate_and_retrieve_duplicate_keys_from_startup` and `visit_duplicate_pubkeys_during_startup`, run separately from the per-slot scan in `generate_index_for_slot`), so their consistency is not statically enforced by the type system — it relies entirely on the correctness of duplicate-key bookkeeping in the accounts index (`accounts_index.rs`, `in_mem_accounts_index.rs`) and on obsolete-account bookkeeping. If duplicate detection or slot-list bookkeeping is ever incorrect (e.g., due to a bug in `populate_and_retrieve_duplicate_keys_from_startup`, a stale/duplicate append vec, or a crafted/corrupted snapshot causing more duplicate data length to be counted than exists in the total), `accounts_data_len_from_duplicates` could exceed `total_accum.accounts_data_len`, causing the subtraction to underflow.

### Impact Explanation
`total_accum.accounts_data_len` becomes `IndexGenerationInfo::accounts_data_len`, which flows to `ReconstructedAccountsDbInfo::accounts_data_len` in `serde_snapshot.rs` and is used to seed the bank's `accounts_data_size` field: [5](#0-4) 

- In a debug build, this underflow triggers an immediate panic (Rust arithmetic overflow check), which would take down every node performing index generation (validator startup / snapshot loading), i.e. a node panic on the honest-node snapshot-load/replay path.
- In a release build (which does not check overflow by default), the subtraction silently wraps to a huge `u64` value. Since `accounts_data_size` is consensus-relevant (used to cap total on-chain account data usage), a corrupted, wrapped value could cause a divergence between the calculated/expected accounts_data_len and reality, silently corrupting a value that downstream consensus logic depends on.

This matches the "node panic" / "silent balance-adjacent state corruption" categories called out as acceptable analogs, and occurs squarely in the AccountsDB index-generation path (in scope per the rules).

### Likelihood Explanation
This code path only executes at startup during `generate_index()` (snapshot load / ledger-tool index rebuild), so it requires that `accounts_data_len_from_duplicates` be miscalculated relative to `total_accum.accounts_data_len` — which should not happen under correct duplicate-key bookkeeping, but the code contains no defensive check to guarantee it, unlike the parallel capitalization computation which is explicitly guarded. The likelihood is directly tied to the correctness of the accounts index duplicate-key tracking; any latent bug there (e.g., a race in `populate_and_retrieve_duplicate_keys_from_startup`, or improper handling of obsolete/marked-obsolete accounts interacting with duplicate detection) would manifest here as an unguarded panic or silent wraparound instead of a clear, actionable error message.

### Recommendation
Replace the unchecked subtraction with a checked variant consistent with the neighboring capitalization logic, e.g.:
```rust
total_accum.accounts_data_len = total_accum
    .accounts_data_len
    .checked_sub(accounts_data_len_from_duplicates)
    .expect("accounts_data_len cannot underflow");
```
This produces a clear diagnostic panic message identifying the invariant violation (as is already done for capitalization) rather than either an unlabelled overflow panic or, worse, a silently wrapped and incorrect `accounts_data_len` in release builds.

### Proof of Concept
No direct external trigger exists to force `accounts_data_len_from_duplicates > total_accum.accounts_data_len` under normal operation; exploiting this requires an underlying defect in duplicate-pubkey/slot-list bookkeeping within `accounts_index.rs`/`in_mem_accounts_index.rs`, which was not verified as independently exploitable within the scope of this review. The concrete, verifiable issue is the code-level inconsistency itself: [6](#0-5)  shows `capitalization` protected by `checked_sub()+expect()` while the structurally identical `accounts_data_len -= accounts_data_len_from_duplicates` on the very next line is not, which is precisely the "not all calculations use SafeMath/checked arithmetic" pattern described in the reference report.

### Citations

**File:** accounts-db/src/accounts_db.rs (L5730-5736)
```rust
        let num_obsolete_accounts_skipped = storage
            .scan_accounts(reader, |offset, account| {
                let data_len = account.data.len();
                stored_size_alive += storage.accounts.calculate_stored_size(data_len);
                let is_account_zero_lamport = account.is_zero_lamport();
                if !is_account_zero_lamport {
                    accounts_data_len += data_len as u64;
```

**File:** accounts-db/src/accounts_db.rs (L5823-5825)
```rust
        accum.num_accounts += insert_info.count as u64;
        accum.insert_time_us += insert_time_us;
        accum.accounts_data_len += accounts_data_len;
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

**File:** accounts-db/src/accounts_db.rs (L6255-6266)
```rust
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
