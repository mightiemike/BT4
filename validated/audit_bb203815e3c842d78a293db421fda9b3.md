### Title
Plain (non-saturating) subtraction in `AccountStorageReader::new` can underflow `num_alive_bytes` when computing snapshot archive tar entry sizes - ([File: accounts-db/src/account_storage_reader.rs])

### Summary
`AccountStorageReader::new`, used when generating snapshot tar archives, computes the number of "alive" bytes to read/report for an account storage file using plain integer subtraction rather than `checked_sub`/`saturating_sub`, unlike the analogous and more carefully-guarded arithmetic elsewhere in `accounts_db` (e.g. `AccountStorageEntry::remove_accounts`, which explicitly asserts before subtracting, and `alive_bytes_exclude_zero_lamport_single_ref_accounts`, which uses `saturating_sub`). If `storage.get_obsolete_bytes(snapshot_slot)` or the tombstone byte total ever exceeds `num_total_bytes`/`num_alive_bytes`, the subtraction underflows.

### Finding Description
In `accounts-db/src/account_storage_reader.rs`: [1](#0-0) 

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

Both subtractions (`num_total_bytes - obsolete_bytes` and `num_alive_bytes -= tombstones * size`) are plain `usize` arithmetic with no `checked_sub`/`saturating_sub`/explicit invariant assertion, in contrast to the pattern used elsewhere in the codebase for the same class of computation, e.g.: [2](#0-1) [3](#0-2) 

This is exactly the reported bug class: "share calculated inside unchecked state; when underflow happens, seller receives huge share." Here, the "share" is `num_alive_bytes`, i.e., the number of bytes reported as belonging to live accounts in a storage file that gets included in the snapshot archive. If underflow occurs, `usize` wraps to a value near `usize::MAX` in a release build (Rust only panics on overflow in debug builds by default; no repo-wide `overflow-checks = true` setting was found in this repo, meaning release/production builds would silently wrap rather than panic).

`num_alive_bytes` is subsequently returned by `len()` and consumed directly in `Read::read()` to bound how many bytes are copied out of the storage file into the snapshot tar stream: [4](#0-3) [5](#0-4) 

`AccountStorageReader` is used directly in the snapshot archiving path (`snapshots/src/archive.rs`) that copies each storage file's bytes into the tar/zstd stream based on `reader.len()`: [6](#0-5) 

### Impact Explanation
If `num_alive_bytes` underflows to a near-`usize::MAX` value:
- `reader.len()` would report an enormous, wrong byte count for the account storage file being archived.
- `Read::read()`'s bound `self.num_total_bytes.saturating_sub(file_offset)` in the no-excluded-account branch caps actual bytes copied from file at EOF, so the immediate read loop would not necessarily read out-of-bounds memory; but any downstream consumer (`io::copy`, size accounting, archive manifest sizes, disk space checks) that trusts `reader.len()` for pre-allocation, capacity checks, or the tar header's declared entry size would compute wildly incorrect (huge) sizes. This is analogous to the "seller gets huge unfair share" — the "share" of bytes attributed to this storage entry becomes disproportionate/wrong, producing corrupted snapshot archives (wrong tar header sizes) that would fail integrity or cause downstream nodes to hang/allocate excessively when unpacking, i.e., a disproportionate storage/CPU cost and honest-node snapshot generation vs. actual content mismatch.
- It could also cause a debug-build panic (denial of service) if debug assertions/overflow checks are enabled in some build profiles, since this is a hard bug even before wrap-around is considered.

### Likelihood Explanation
Reaching this code path requires that `get_obsolete_bytes(snapshot_slot)` (sum of obsolete-account stored sizes as of `snapshot_slot`) or the tombstone-count-derived byte total exceed the storage's actual total/alive bytes. This should not happen under correct bookkeeping of obsolete/tombstone accounting, so likelihood under normal, non-buggy operation is low. However, since there is no defensive check (assert, `checked_sub`, or `saturating_sub`) as there is in the sibling `remove_accounts`/`alive_bytes_exclude_zero_lamport_single_ref_accounts` functions, any latent bug in the obsolete-accounts or tombstone-tracking bookkeeping (e.g., double-counting an account as both obsolete and tombstoned, or a slot-filtering edge case in `filter_obsolete_accounts`) would silently manifest here as a wrong/huge value at snapshot-archive time rather than being caught immediately, and only during snapshot generation (a normal, frequently-executed, unprivileged operational path), making this a meaningful robustness gap.

### Recommendation
Replace the plain subtractions in `AccountStorageReader::new` with `checked_sub` (returning an `io::Error` / propagating a hard error) or, at minimum, `saturating_sub` combined with a `debug_assert!`/invariant check mirroring `AccountStorageEntry::remove_accounts`'s explicit assertion pattern, so that any bookkeeping inconsistency between obsolete/tombstone byte counts and total/alive bytes is caught deterministically instead of silently producing an incorrect (potentially huge) `num_alive_bytes` value that propagates into snapshot archive generation.

### Proof of Concept
Not independently reproducible from static analysis alone: it requires constructing a state where `storage.get_obsolete_bytes(snapshot_slot)` (sum of stored sizes of obsolete-account entries filtered by `slot <= snapshot_slot`) plus, when tombstones are excluded, `tombstone_offsets.len() * tombstone_stored_size`, exceeds `storage.accounts.len()` / the running `num_alive_bytes`. This would require a bookkeeping defect elsewhere (e.g., in `ObsoleteAccounts::mark_accounts_obsolete` or tombstone-offset tracking) that double-counts bytes; I could not find and did not verify such a triggering defect elsewhere in the explored code, so this should be treated as a hardening/robustness gap analogous to the reported bug class rather than a confirmed exploitable underflow with a concrete external trigger.

### Citations

**File:** accounts-db/src/account_storage_reader.rs (L100-126)
```rust
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

**File:** accounts-db/src/account_storage_reader.rs (L139-145)
```rust
    pub fn len(&self) -> usize {
        self.num_alive_bytes
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
```

**File:** accounts-db/src/account_storage_reader.rs (L165-173)
```rust
            // Cannot read beyond the end of the buffer
            let bytes_left_in_buffer = buf_len.saturating_sub(total_read);

            // Cannot read beyond the next excluded account or the end of the file
            let bytes_to_read_from_file = if let Some((excluded_start, _)) = next_excluded_account {
                excluded_start.saturating_sub(file_offset)
            } else {
                self.num_total_bytes.saturating_sub(file_offset)
            };
```

**File:** accounts-db/src/account_storage_entry.rs (L227-233)
```rust
    /// Return the "alive_bytes" minus "zero_lamport_single_ref_accounts bytes".
    pub(crate) fn alive_bytes_exclude_zero_lamport_single_ref_accounts(&self) -> usize {
        let zero_lamport_dead_bytes = self
            .accounts
            .dead_bytes_due_to_zero_lamport_single_ref(self.num_zero_lamport_single_ref_accounts());
        self.alive_bytes().saturating_sub(zero_lamport_dead_bytes)
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L268-289)
```rust
    /// Removes `num_bytes` and `num_accounts` from the storage,
    /// and returns the remaining number of accounts.
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

**File:** snapshots/src/archive.rs (L1-25)
```rust
use {
    crate::{
        ArchiveFormat, Result, SnapshotArchiveKind, error::ArchiveSnapshotPackageError,
        multiframe::MultiFrameZstdWriter, paths, snapshot_archive_info::SnapshotArchiveInfo,
        snapshot_hash::SnapshotHash,
    },
    agave_fs::{
        FileSize, buffered_reader::FileBufRead as _, buffered_writer::large_file_buf_writer,
        io_setup::IoSetupState,
    },
    log::info,
    solana_accounts_db::{
        account_storage::AccountStoragesOrderer,
        account_storage_entry::AccountStorageEntry,
        account_storage_reader::{
            ACCOUNT_STORAGE_MAX_BUFFER_SIZE, AccountStorageReader, TombstonesFilter,
            open_storage_files, storage_file_buf_reader,
        },
        accounts_file::AccountsFile,
    },
    solana_clock::Slot,
    solana_measure::measure::Measure,
    solana_metrics::datapoint_info,
    std::{fs, io::Write, path::Path, sync::Arc},
};
```
