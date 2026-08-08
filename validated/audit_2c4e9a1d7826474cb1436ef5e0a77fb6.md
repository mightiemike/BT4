### Title
Unchecked subtraction in `AccountStorageReader::new` can underflow `num_alive_bytes` during snapshot generation - (File: accounts-db/src/account_storage_reader.rs)

### Summary
`AccountStorageReader::new`, used when writing account-storage files into a snapshot archive, computes the number of "alive" bytes to read from a storage file by subtracting the sizes of obsolete and tombstone accounts from the storage's total written bytes. Unlike every other lamport/byte-accounting computation in `accounts-db` (capitalization sums, `remove_accounts`, `alive_bytes_after_shrink`, etc.), which use `checked_add`/`checked_sub` with an explicit `.expect(...)` panic or `saturating_sub`, this function uses plain `-` subtraction twice, with no overflow guard.

### Finding Description
In `AccountStorageReader::new` [1](#0-0) :

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

Both `num_total_bytes - storage.get_obsolete_bytes(snapshot_slot)` and `num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size` are raw, unchecked `usize` subtractions. `get_obsolete_bytes` itself sums per-offset stored sizes, clamped per-entry via `.min(self.accounts.len() - offset)` [2](#0-1) , but there is no guarantee at the call site that the *sum* of obsolete bytes plus tombstone bytes cannot reach or exceed `num_total_bytes`, particularly since tombstone accounting and obsolete-account accounting are maintained via two independent, separately-locked sets (`obsolete_accounts` and `tombstone_offsets`) that can, per the codebase's own documented caveat, double-count the same dead account in two places (see the comment on `zero_lamport_single_ref_offsets` acknowledging accounts can be "dead twice" in `num_alive_bytes` accounting [3](#0-2) ). If the combined removed-bytes total ever exceeds `num_total_bytes`, this subtraction underflows.

This is the direct analog of the reported bug class: an arithmetic operation on an untrusted/derived accounting value performed without an overflow/underflow check, mirroring the report's `swap_usd_amount` overflow and its call for validating balances before subtraction (the `dispensing_custody` underflow check). Every comparable accounting path elsewhere in `accounts-db` (e.g. `AccountStorageEntry::remove_accounts`, which asserts `num_bytes <= prev_num_alive_bytes` before subtracting [4](#0-3) , and capitalization sums that use `checked_add`/`checked_sub` with `.expect("capitalization cannot overflow")` [5](#0-4) ) was deliberately hardened against exactly this failure mode, but `AccountStorageReader::new` was not.

### Impact Explanation
`AccountStorageReader` is constructed by `snapshots/src/archive.rs` when serializing account storage files into a snapshot archive (both full and incremental snapshots), and `self.len()` (`num_alive_bytes`) determines how many bytes of the storage file are read via the `Read` implementation for archiving. If the subtraction underflows in a release build (no `overflow-checks` setting was found in the workspace `Cargo.toml`, so `usize` subtraction wraps silently rather than panicking), `num_alive_bytes` becomes an enormous value near `usize::MAX`. This can cause the reader to attempt to read far more bytes than the storage file actually contains, producing a corrupted/oversized snapshot archive, a validator panic (e.g. an I/O error surfaced as an unwrap/panic elsewhere in the archiving pipeline), or a snapshot whose contents diverge from what replay would produce — i.e. an honest-node snapshot-vs-replay mismatch, one of the explicitly accepted impact categories.

### Likelihood Explanation
Reaching this requires a slot's tombstone/obsolete-account bookkeeping to have overlapping or over-counted dead bytes relative to the storage's total written bytes at the moment a snapshot is generated. Given the codebase's own comment acknowledging that a zero-lamport single-ref account can be counted as dead through two separate mechanisms simultaneously ("we will count this account as 'dead' twice" [3](#0-2) ), and that snapshot generation happens on a normal, periodic, unprivileged-triggerable path (any workload of ordinary user transactions that creates/closes/rewrites accounts drives shrink, cleaning, and snapshotting), the preconditions are plausible under specific interleavings of shrink/clean and snapshot-slot selection, though not trivially demonstrated without deeper testing of the shrink/tombstone/obsolete interaction. This is assessed as a real, reachable code-path issue rather than purely theoretical, but the exact triggering interleaving was not empirically constructed here.

### Recommendation
Replace the unchecked subtractions in `AccountStorageReader::new` with `checked_sub` (or `saturating_sub`) and either clamp to zero or fail loudly (matching the pattern used elsewhere in `accounts-db`, e.g. `remove_accounts`'s assertion), specifically:
```rust
let mut num_alive_bytes = num_total_bytes
    .checked_sub(storage.get_obsolete_bytes(snapshot_slot))
    .expect("obsolete bytes cannot exceed total bytes");
...
num_alive_bytes = num_alive_bytes
    .checked_sub(tombstone_offsets.len() * tombstone_stored_size)
    .expect("tombstone bytes cannot exceed remaining alive bytes");
```
Additionally, audit the interaction between `obsolete_accounts` and `tombstone_offsets` bookkeeping to ensure a single dead account cannot be double-counted across both sets when computing snapshot-time alive-byte totals.

### Proof of Concept
Not independently constructed; a full reproduction would require driving `AccountStorageEntry::obsolete_accounts` and `tombstone_offsets` into a state where their combined byte totals exceed `storage.accounts.len()` for a given `snapshot_slot`, then invoking snapshot archive generation (`snapshots/src/archive.rs`, which constructs `AccountStorageReader`) on that storage. This was not verified against a live build in this analysis; the code path and unchecked arithmetic are confirmed by direct inspection, but the exact sequence of shrink/clean/tombstone operations needed to trigger the overlap was not traced end-to-end within the scope of this review.

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

**File:** accounts-db/src/account_storage_entry.rs (L36-46)
```rust
    /// offsets to accounts that are zero lamport single ref (ZLSR) stored in this
    /// storage. These are still alive. But, shrink will be able to remove them.
    ///
    /// NOTE: It's possible that one of these zero lamport single ref accounts
    /// could be written in a new transaction (and later rooted & flushed) and a
    /// later clean runs and marks this account dead before this storage gets a
    /// chance to be shrunk, thus making the account dead in both "num_alive_bytes"
    /// and as a zero lamport single ref. If this happens, we will count this
    /// account as "dead" twice. However, this should be fine. It just makes
    /// shrink more likely to visit this storage.
    zero_lamport_single_ref_offsets: RwLock<IntSet<Offset>>,
```

**File:** accounts-db/src/account_storage_entry.rs (L149-160)
```rust
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

**File:** accounts-db/src/account_storage_entry.rs (L270-289)
```rust
    pub(crate) fn remove_accounts(&self, num_bytes: usize, num_accounts: usize) -> usize {
        let prev_num_alive_bytes = self.num_alive_bytes.fetch_sub(num_bytes, Ordering::Release);
        let prev_num_alive_accounts = self
            .num_alive_accounts
            .fetch_sub(num_accounts, Ordering::Release);

        // enforce invariant that we're not removing too many bytes or accounts
        assert!(
            num_bytes <= prev_num_alive_bytes && num_accounts <= prev_num_alive_accounts,
            "Too many bytes or accounts removed from storage! slot: {}, id: {}, initial num alive \
             bytes: {prev_num_alive_bytes}, initial num alive accounts: \
             {prev_num_alive_accounts}, num bytes removed: {num_bytes}, num accounts removed: \
             {num_accounts}",
            self.slot,
            self.id,
        );

        // SAFETY: subtraction is safe since we just asserted num_accounts <= prev_num_accounts
        prev_num_alive_accounts - num_accounts
    }
```

**File:** accounts-db/src/accounts_db.rs (L6108-6112)
```rust
        total_accum.capitalization = total_accum
            .capitalization
            .checked_sub(capitalization_from_duplicates)
            .expect("capitalization cannot underflow");
        total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
```
