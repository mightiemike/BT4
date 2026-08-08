## Title
Unchecked subtraction in `AccountStorageReader::new` can underflow and panic during snapshot generation - (File: accounts-db/src/account_storage_reader.rs)

### Summary
`AccountStorageReader::new` computes the number of alive bytes to serialize into a snapshot using two raw, non-saturating subtractions instead of the `checked_sub`/`saturating_sub` pattern used everywhere else in `accounts_db.rs`/`account_storage_entry.rs` for the same kind of accounting (`alive_bytes`, `num_alive_accounts`, capitalization, etc.). This mirrors the C4 finding class of "rounding/accounting mismatch causing a raw subtraction to underflow," except here the two subtracted quantities are independent, unlocked counters (`obsolete_accounts` and `tombstone_offsets`) computed at different points against a storage that can be concurrently mutated by shrink/clean, rather than by a fixed formula.

### Finding Description
`AccountStorageReader::new` is used when serializing an account storage file into a snapshot archive: [1](#0-0) 

```rust
let num_total_bytes = storage.accounts.len();
let mut num_alive_bytes = num_total_bytes - storage.get_obsolete_bytes(snapshot_slot);
...
if tombstones_filter == TombstonesFilter::Exclude {
    let tombstone_stored_size = storage.accounts.calculate_stored_size(0);
    let tombstone_offsets = storage.tombstone_offsets_read_lock();
    num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size;
    ...
}
```

Both `num_total_bytes - storage.get_obsolete_bytes(snapshot_slot)` (line 101) and `num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size` (line 120) are unchecked `usize` subtractions. `get_obsolete_bytes` sums up sizes from the `obsolete_accounts` set under its own lock [2](#0-1) , and `tombstone_offsets` is read under a separate lock. These two collections are populated by different code paths (`obsolete_accounts.mark_accounts_obsolete` vs. `batch_insert_tombstone_offsets` during shrink) and are not guaranteed to be disjoint by any single invariant enforced at this call site; the comment on `zero_lamport_single_ref_offsets` explicitly acknowledges that overlap between "dead" bookkeeping sets is possible in this codebase ("we will count this account as dead twice... this should be fine" for the ZLSR/alive_bytes case). If `get_obsolete_bytes(snapshot_slot)` ever returns a value larger than `num_total_bytes` (e.g. due to a race between `mark_accounts_obsolete` and this reader being constructed on the same storage, or a size computed from a stale/incorrect `data_len` via `calculate_stored_size`), or if `tombstone_offsets.len() * tombstone_stored_size` exceeds the already-reduced `num_alive_bytes`, the subtraction underflows.

This differs qualitatively from every other alive-byte/count adjustment in `accounts_db.rs`, which is consistently guarded by an `assert!` invariant check (`AccountStorageEntry::remove_accounts`, `account_storage_entry.rs:270-289`) or a `saturating_sub` (`alive_bytes_exclude_zero_lamport_single_ref_accounts`, `account_storage_entry.rs:228-233`; `should_not_shrink`/`is_shrinking_productive`, `accounts_db.rs:5003-5043`). `AccountStorageReader::new` is the outlier that performs the arithmetic without any such protection.

### Impact Explanation
In a debug/overflow-checked build, an underflow here panics, crashing the node process during snapshot writing — a node-panic against an honest validator/RPC node, matching the "node panic" impact bucket in this program's validate criteria. In a release build without overflow checks, the subtraction wraps to a huge `usize`, so `AccountStorageReader::len()` (used to size buffers and to compute snapshot content sizes, e.g. `bytes_written == reader.len()` in the associated tests) would report an enormous, wrong value; this can corrupt the produced snapshot file's declared length, producing a snapshot that disagrees with what replay would reconstruct — an honest-node snapshot generation defect. Because this is inside the general snapshot serialization path (used for both full and incremental snapshots, not sBPF/RPC/vote-related), it falls within the requested scope of "snapshot generation and rebuild."

### Likelihood Explanation
This requires the two independently-tracked exclusion sets (`obsolete_accounts` bytes and `tombstone_offsets` bytes) to disagree with `num_total_bytes`/`num_alive_bytes` at the moment a snapshot reader is constructed for a given storage — for example, through a race window during concurrent shrink/clean activity or a bug in how tombstone/obsolete accounting is kept in sync, given the codebase itself documents (elsewhere, for ZLSR accounts) that double-counting across these separate "dead" bookkeeping structures is an accepted/known possibility. I could not fully verify from the available code whether such an overlap between `obsolete_accounts` and `tombstone_offsets` (or a stale `data_len` causing an inflated `get_obsolete_bytes`) is actually reachable in a single-threaded, well-ordered call sequence, since the exact producer/consumer synchronization for snapshot generation vs. shrink was not fully traced in this review — this is the key uncertainty limiting confidence versus a purely theoretical concern.

### Recommendation
Replace both raw subtractions in `AccountStorageReader::new` with `saturating_sub` (consistent with the rest of the codebase's defensive style for alive-byte accounting), or add an explicit `assert!`/`debug_assert!` invariant check analogous to `AccountStorageEntry::remove_accounts` before subtracting, so that any accounting mismatch is caught with a clear diagnostic rather than silently wrapping or panicking with an opaque arithmetic-overflow message.

### Proof of Concept
Not independently reproduced; the analysis is based on static code review of `accounts-db/src/account_storage_reader.rs` versus the guarded-subtraction pattern used consistently elsewhere in `accounts-db/src/account_storage_entry.rs` and `accounts-db/src/accounts_db.rs`. A concrete PoC would need to construct an `AccountStorageEntry` where `tombstone_offsets` and `obsolete_accounts` byte totals sum to more than `num_total_bytes`/`num_alive_bytes` at the time `AccountStorageReader::new` runs, which was not confirmed achievable through the code paths reviewed.

### Citations

**File:** accounts-db/src/account_storage_reader.rs (L94-126)
```rust
    pub fn new(
        storage: &AccountStorageEntry,
        snapshot_slot: Option<Slot>,
        tombstones_filter: TombstonesFilter,
        file_reader: &'r mut R,
    ) -> io::Result<Self> {
        let num_total_bytes = storage.accounts.len();
        let mut num_alive_bytes = num_total_bytes - storage.get_obsolete_bytes(snapshot_slot);

        let mut sorted_excluded_accounts: Vec<_> = storage
            .obsolete_accounts_read_lock()
            .filter_obsolete_accounts(snapshot_slot)
            .collect();

        // Convert the length to the size
        sorted_excluded_accounts
            .iter_mut()
            .for_each(|(_offset, len)| {
                *len = storage.accounts.calculate_stored_size(*len);
            });

        if tombstones_filter == TombstonesFilter::Exclude {
            // Tombstones are zero-lamport accounts, which store no data, so every
            // tombstone record has the fixed stored size of a data-less account.
            let tombstone_stored_size = storage.accounts.calculate_stored_size(0);
            let tombstone_offsets = storage.tombstone_offsets_read_lock();
            num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size;
            sorted_excluded_accounts.extend(
                tombstone_offsets
                    .iter()
                    .map(|offset| (*offset, tombstone_stored_size)),
            );
        }
```

**File:** accounts-db/src/account_storage_entry.rs (L146-160)
```rust
    /// Returns the number of bytes that were marked obsolete as of the passed
    /// in slot or earlier. If slot is None, then slot will be assumed to be the
    /// max root, and all obsolete bytes will be returned.
    pub fn get_obsolete_bytes(&self, slot: Option<Slot>) -> usize {
        let obsolete_bytes: usize = self
            .obsolete_accounts_read_lock()
            .filter_obsolete_accounts(slot)
            .map(|(offset, data_len)| {
                self.accounts
                    .calculate_stored_size(data_len)
                    .min(self.accounts.len() - offset)
            })
            .sum();
        obsolete_bytes
    }
```
