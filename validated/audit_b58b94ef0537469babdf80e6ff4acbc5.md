### Title
Unchecked subtraction of `accounts_data_len_from_duplicates` in `generate_index()` can underflow `accounts_data_len` during snapshot/index rebuild - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::generate_index()`, which is executed during startup when rebuilding the accounts index from a snapshot, accumulates `accounts_data_len` across all storages and then subtracts the amount attributable to duplicate pubkey/account versions found during startup deduplication. Unlike the sibling `capitalization` field in the very same function, which is defended with `checked_sub(...).expect("capitalization cannot underflow")`, the `accounts_data_len` subtraction is a plain, unchecked `-=` operation. This is directly analogous to the reported UniswapV2-style arithmetic that "is known to overflow/underflow" and either silently wraps or panics depending on build configuration — exactly the bug class flagged in the external report.

### Finding Description
In `AccountsDb::generate_index()`: [1](#0-0) 

```rust
total_accum.lt_hash.mix_out(&duplicates_lt_hash.0);
total_accum.capitalization = total_accum
    .capitalization
    .checked_sub(capitalization_from_duplicates)
    .expect("capitalization cannot underflow");
total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
info!("accounts data len: {}", total_accum.accounts_data_len);
```

`total_accum.accounts_data_len` is a `u64` accumulated per-storage while scanning accounts: [2](#0-1) 

and `accounts_data_len_from_duplicates` is derived from `visit_duplicate_pubkeys_during_startup`, which sums the data length of all-but-the-newest version of every duplicated pubkey encountered while indexing storages at startup (used by `IndexGenerationAccumulator`): [3](#0-2) 

The code author explicitly recognized this exact hazard for the `capitalization` field ("Panics if capitalization overflows a u64" and the accompanying `checked_sub`/`.expect(...)` guard at line 6108-6111 and again at 4728-4730), but the parallel `accounts_data_len -= accounts_data_len_from_duplicates` on line 6112 was left as a raw subtraction with no `checked_sub`, `saturating_sub`, or `wrapping_sub`. If `accounts_data_len_from_duplicates` is ever computed to be larger than `total_accum.accounts_data_len` (e.g., due to a bug in duplicate-detection/counting logic, an edge case in how obsolete/zero-lamport accounts are excluded from the two independently-computed totals, or a corrupted/edge-case snapshot), this line will:
- In a build with overflow checks enabled (debug builds, and potentially any build where the workspace enables `overflow-checks = true`), **panic**, aborting startup/snapshot rebuild entirely — a full node-panic denial of the `generate_index()` path used both for normal validator startup from snapshot and for `ledger-tool`.
- In a release build without overflow checks, **silently wrap** to a huge `u64` value near `u64::MAX`, which is then returned as `IndexGenerationInfo::accounts_data_len` and propagated into `Bank::new_from_snapshot(..., reconstructed_accounts_db_info.accounts_data_len, ...)`: [4](#0-3) 

This corrupted `accounts_data_len` becomes part of bank state used for accounts-data-length enforcement/rent-related accounting, producing silent state divergence between the freshly rebuilt bank and what would be computed by normal replay — i.e., a snapshot-vs-replay mismatch on an honest node with no adversarial input required.

### Impact Explanation
This exactly matches the report's bug class: an arithmetic operation "known to" or capable of overflowing/underflowing that is not wrapped in a safe/checked construct, while the immediately adjacent, structurally identical computation (`capitalization`) *was* hardened with `checked_sub`. Depending on build profile this either:
1. Panics the node during snapshot load / index generation (denial of the startup path), or
2. Silently wraps to a huge, wrong `accounts_data_len`, causing a stored/derived value to diverge from what live replay would produce — a state-divergence class explicitly called out as acceptable impact ("honest-node snapshot-vs-replay mismatch").

### Likelihood Explanation
`generate_index()` runs on every validator startup that loads from a snapshot and in `ledger-tool`, so the code path itself is common; what's uncertain is whether `accounts_data_len_from_duplicates` can practically exceed `total_accum.accounts_data_len` given current duplicate-detection invariants. I could not fully verify the invariant that guarantees `accounts_data_len_from_duplicates <= total_accum.accounts_data_len` holds in all cases (e.g., interaction with obsolete-account skipping, zero-lamport handling, or partial/limited snapshot loads via `limit_load_slot_count_from_snapshot`), which would require deeper tracing of `visit_duplicate_pubkeys_during_startup` than the available index snippets allowed me to confirm. The fact that the developers added an explicit `checked_sub` guard for the structurally parallel `capitalization` field, but not for `accounts_data_len`, suggests this was an oversight rather than a proven-safe invariant.

### Recommendation
Replace the raw subtraction with a checked/saturating operation mirroring the `capitalization` handling immediately above it, e.g.:
```rust
total_accum.accounts_data_len = total_accum
    .accounts_data_len
    .checked_sub(accounts_data_len_from_duplicates)
    .expect("accounts_data_len cannot underflow");
```
This makes the failure mode explicit and debuggable (a clear panic message) rather than either an opaque overflow panic or a silent wraparound that corrupts bank state.

### Proof of Concept
Not independently reproduced — the index/documents available did not let me construct a concrete snapshot/storage layout that forces `accounts_data_len_from_duplicates > total_accum.accounts_data_len`. This would require exercising `visit_duplicate_pubkeys_during_startup` with a snapshot containing duplicate-pubkey account versions engineered so that the summed data length of "old" duplicate versions exceeds the total accumulated `accounts_data_len`, which is beyond what could be confirmed via static code inspection alone in this session.

### Citations

**File:** accounts-db/src/accounts_db.rs (L400-426)
```rust
/// Accumulator for the values produced while generating the index
#[derive(Debug)]
struct IndexGenerationAccumulator {
    insert_time_us: u64,
    num_accounts: u64,
    accounts_data_len: u64,
    all_accounts_are_zero_lamports_slots: u64,
    /// List of slots with only zero lamports accounts and indices into `storages` used in `generate_index`
    slots_with_only_zero_lamport_accounts: Vec<(Slot, usize)>,
    storage_info: StorageSizeAndCountList,
    /// Number of accounts in this slot that didn't already exist in the index
    num_did_not_exist: u64,
    /// Number of accounts in this slot that already existed, and were in-mem
    num_existed_in_mem: u64,
    /// Number of accounts in this slot that already existed, and were on-disk
    num_existed_on_disk: u64,
    /// The accounts lt hash for the set of accounts processed using this accumulator
    lt_hash: LtHash,
    /// The capitalization for the set of accounts processed using this accumulator.
    /// Needs to be u128 as it may temporarily overflow u64 due to
    /// all duplicates being summed before being removed.
    capitalization: u128,
    /// The number of accounts in this slot that were skipped when generating the index as they
    /// were already marked obsolete in the account storage entry
    num_obsolete_accounts_skipped: u64,
    slot_arena: IndexGenerationSlotArena,
}
```

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

**File:** runtime/src/serde_snapshot.rs (L977-986)
```rust
    let bank = Bank::new_from_snapshot(
        bank_rc,
        genesis_config,
        runtime_config,
        bank_fields,
        leader_for_tests,
        debug_keys,
        reconstructed_accounts_db_info.accounts_data_len,
        epoch_stakes,
    );
```
