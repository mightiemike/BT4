### Title
Unchecked subtraction in `AccountStorageReader::new` can panic during snapshot archive generation - ([File: accounts-db/src/account_storage_reader.rs])

### Summary
`AccountStorageReader::new`, which is used during full/incremental snapshot archive creation, computes `num_alive_bytes` with plain (non-checked/non-saturating) subtraction from `num_total_bytes` and from an obsolete/tombstone byte count. If those "dead" byte counts are ever equal to or exceed the total storage bytes due to a stale/duplicated bookkeeping edge case, the subtraction underflows and panics, analogous to the reported Uniswap V3 `getFeeGrowthInside` underflow-revert bug where a delta between two related-but-not-strictly-ordered accumulators reverts instead of wrapping or saturating.

### Finding Description
`AccountStorageReader::new` computes the number of alive bytes to read out of a storage file for producing a snapshot (archive or fastboot storage): [1](#0-0) 

Line 101 performs `num_total_bytes - storage.get_obsolete_bytes(snapshot_slot)`, and line 120 performs `num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size`, both as plain `usize` subtraction. In debug builds this panics on underflow; in release builds it wraps to a huge value, corrupting the computed `len()` used by the `Read` impl and callers.

`get_obsolete_bytes` sums `calculate_stored_size(data_len).min(self.accounts.len() - offset)` over all obsolete accounts filtered by `snapshot_slot`: [2](#0-1) 

This value is expected to always be `<= storage.accounts.len()` by invariant (obsolete accounts are a subset of the storage's total bytes, and tombstones are disjoint from obsolete accounts). However, this invariant is enforced only by convention across the obsolete-accounts tracking (`ObsoleteAccounts::mark_accounts_obsolete`), the tombstone-offset set (`batch_insert_tombstone_offsets`), and the offset bookkeeping in `AccountStorageEntry`, none of which are checked at the point of use in `AccountStorageReader::new`. Unlike the analogous `alive_bytes()`/`remove_accounts()` path in the same file which explicitly asserts before subtracting (see `remove_accounts` at lines 268-289 using `assert!` before `prev_num_alive_accounts - num_accounts`), and unlike `alive_bytes_exclude_zero_lamport_single_ref_accounts` which uses `saturating_sub`, this snapshot-reader code path has no such guard.

Because obsolete-account marking and tombstone-offset tracking are independently maintained sets that can, in principle, overlap or double count under bugs in concurrent shrink/clean/flush interactions (the same general class of "computed delta between two evolving cumulative sets" that caused the Uniswap fee-growth underflow), any accounting drift here directly translates into a panic (or silent wraparound) rather than a graceful degradation.

`AccountStorageReader` is invoked directly by the snapshot archiving path in `snapshots/src/archive.rs` (used for both full and incremental snapshot generation): [3](#0-2) 

### Impact Explanation
If the subtraction underflows:
- In debug/test builds, the validator process panics while generating a snapshot archive, which is a node crash — a serious validator liveness issue during routine snapshot-generation operation.
- In release builds (where Rust's default `overflow-checks` may be disabled), the subtraction wraps to a near-`usize::MAX` value, making `num_alive_bytes` (and thus `reader.len()`) wildly incorrect. This can produce a corrupted/truncated snapshot archive (wrong byte count reported to `io::copy`/archive writer), risking snapshot-vs-replay divergence for any node/consumer that later restores from that snapshot.

This satisfies the "node panic" and "disproportionate/incorrect snapshot generation" impact categories called out in scope.

### Likelihood Explanation
Likelihood is currently unproven/theoretical: I was not able to trace a concrete code path in the reachable code that causes `get_obsolete_bytes(snapshot_slot)` plus tombstone bytes to exceed `storage.accounts.len()` under normal operation — the existing test suite (`test_account_storage_reader_with_excluded_accounts`, `test_account_storage_reader_filter_by_slot`) exercises many combinations of tombstones/obsolete accounts and passes, implying the invariant normally holds. The finding is analogous to the reported bug class (an implicit reliance on ordering/invariants between two independently-updated byte/account counters, with no defensive check at the point of subtraction) but I could not confirm a reachable double-counting or accounting-drift scenario between the obsolete-accounts tracking and tombstone-offset tracking that would actually break the invariant in production. This should be treated as a defensive-coding gap rather than a confirmed exploitable underflow.

### Recommendation
- Replace the plain subtractions at accounts-db/src/account_storage_reader.rs lines 101 and 120 with `saturating_sub` (consistent with `alive_bytes_exclude_zero_lamport_single_ref_accounts`), or add an explicit `debug_assert!`/`assert!` that `obsolete_bytes + tombstone_bytes <= num_total_bytes` before subtracting, mirroring the pattern already used in `AccountStorageEntry::remove_accounts`.
- Audit whether tombstone offsets and obsolete-account offsets can overlap (i.e., whether a tombstone account can also be marked obsolete for some `snapshot_slot`), which would cause double-subtraction of the same bytes.

### Proof of Concept
Not reproducible with current information: no reachable path was found in the codebase that causes `get_obsolete_bytes` + tombstone bytes to exceed `num_total_bytes` under normal accounts-db operation. Confirming exploitability would require constructing a scenario where the obsolete-accounts set and tombstone-offsets set become inconsistent with the storage's actual byte accounting (e.g., via a race between `mark_accounts_obsolete`, `batch_insert_tombstone_offsets`, and shrink/clean operations), which requires deeper runtime/concurrency analysis than static code review allows.

### Citations

**File:** accounts-db/src/account_storage_reader.rs (L99-126)
```rust
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

**File:** accounts-db/src/account_storage_entry.rs (L150-160)
```rust
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

**File:** snapshots/src/archive.rs (L12-20)
```rust
    solana_accounts_db::{
        account_storage::AccountStoragesOrderer,
        account_storage_entry::AccountStorageEntry,
        account_storage_reader::{
            ACCOUNT_STORAGE_MAX_BUFFER_SIZE, AccountStorageReader, TombstonesFilter,
            open_storage_files, storage_file_buf_reader,
        },
        accounts_file::AccountsFile,
    },
```
