## Analysis

The tests confirm the invariant expected: `tombstone_stored_size = calculate_stored_size(0)` is always subtracted unclamped from `num_alive_bytes` when computing the reader length used to size and copy data for snapshot archives.

### Title
Unclamped tombstone-size subtraction in `AccountStorageReader::new` can underflow `num_alive_bytes` when a tombstone is the file's unaligned tail entry - (File: accounts-db/src/account_storage_reader.rs)

### Summary
`AccountStorageReader::new` computes the number of bytes to read for a storage when building a snapshot/archive stream. It first subtracts obsolete-account bytes using a size that is *clamped* to the remaining file length (`get_obsolete_bytes` uses `.min(self.accounts.len() - offset)`), because the docstring elsewhere explicitly notes that "written_bytes may not be aligned for the last account." However, when `TombstonesFilter::Exclude` is set, the code subtracts `tombstone_offsets.len() * tombstone_stored_size` — using the *unclamped*, always-rounded `calculate_stored_size(0)` — without applying the same tail-clamping logic used for obsolete accounts.

### Finding Description
`calculate_stored_size(0)` returns `u64_align!(STORE_META_OVERHEAD)`, an aligned constant [1](#0-0) . This is combined arithmetically with `num_total_bytes = storage.accounts.len()`, the storage's true, potentially non-aligned length, mirroring the reported bug class: an aggregate value maintained in one "precision"/rounding basis (raw byte length of the file) is decremented by a quantity computed under a different, always-rounded basis (a fixed aligned constant), without reconciling the two.

```
let mut num_alive_bytes = num_total_bytes - storage.get_obsolete_bytes(snapshot_slot);
...
if tombstones_filter == TombstonesFilter::Exclude {
    let tombstone_stored_size = storage.accounts.calculate_stored_size(0);
    let tombstone_offsets = storage.tombstone_offsets_read_lock();
    num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size;
    ...
}
``` [2](#0-1) 

Compare this with `get_obsolete_bytes`, which explicitly clamps each obsolete account's stored size to the remaining bytes in the file to guard exactly this class of mismatch:
```
.map(|(offset, data_len)| {
    self.accounts
        .calculate_stored_size(data_len)
        .min(self.accounts.len() - offset)
})
.sum();
``` [3](#0-2) 

No equivalent `.min(...)` clamp exists for the tombstone-size subtraction path. `num_alive_bytes` is a plain `usize`, so if the aligned tombstone size ever exceeds the bytes actually remaining for that tombstone in the file (e.g., a tombstone at/near the very end of the underlying `AppendVec`, where `written_bytes` is not aligned per the documented invariant used elsewhere in the crate — see the shrink-ancient comment noting exactly this) [4](#0-3) , the subtraction underflows.

### Impact Explanation
An unclamped `usize` underflow in `num_alive_bytes` used to drive `AccountStorageReader::len()` (and hence `Read::read`, which also uses `self.num_total_bytes.saturating_sub(...)` and `excluded_size.min(self.num_total_bytes - excluded_start)` for skip logic) [5](#0-4)  would produce either:
- A panic (integer overflow check) on debug/validator builds compiled with overflow checks, crashing the node while generating a snapshot archive, or
- A silently corrupted, enormous `num_alive_bytes` value in release builds (wrapping), causing the reader to attempt to read far more bytes than the file contains, producing a corrupted/truncated snapshot archive that would fail rehydration or cause a hash/capitalization mismatch on the node that later loads it.

This falls squarely in the "honest-node snapshot generation" and "node panic" impact categories permitted by the rules.

### Likelihood Explanation
This is an internal accounting helper invoked whenever incremental snapshots are archived with `TombstonesFilter::Exclude`, which is used specifically to drop already-known-deleted zero-lamport tombstone accounts from being written into archives [6](#0-5) . Whether a tombstone can actually land as the storage's final, sub-aligned entry (making the underflow reachable) depends on `AppendVec`/`AccountsFile` write layout details not fully visible in the indexed portion of the codebase; the crate's own comments (`ancient_append_vecs.rs`) confirm the underlying invariant ("written_bytes may not be aligned for the last account") that the parallel `get_obsolete_bytes` clamp was written to guard against, but I could not fully trace every code path that populates `tombstone_offsets` to definitively confirm or rule out a tombstone occupying that exact tail position. This uncertainty should be resolved by a deeper trace of `batch_insert_tombstone_offsets` call sites and shrink/carry-forward logic before treating this as fully proven at Medium+ severity.

### Recommendation
Apply the same tail-clamping used in `get_obsolete_bytes` when computing the tombstone byte deduction, e.g. clamp each tombstone's `tombstone_stored_size` to `self.accounts.len() - offset` per tombstone (rather than a single unclamped constant multiplied by count), and use `saturating_sub` for the `num_alive_bytes -=` update as defense in depth.

### Proof of Concept
Not able to construct a concrete reproducing scenario from the indexed code alone — this requires confirming, via the `AccountsFile`/`AppendVec` write/shrink code paths, whether a tombstone offset can ever be assigned to the final, unaligned tail slot of a storage file. The existing unit tests in this file (`test_account_storage_reader_with_excluded_accounts`) only exercise scenarios where `current_len -= num_tombstones * storage.accounts.calculate_stored_size(0)` [7](#0-6)  without ever placing a tombstone at the misaligned tail, so this specific edge case is not covered by current tests.

### Citations

**File:** accounts-db/src/append_vec.rs (L851-859)
```rust
    /// Returns the number of bytes required to store an account with the passed in `data_len`.
    ///
    /// This includes:
    /// - the fixed-size per-account metadata
    /// - possible alignment padding bytes before the next account
    #[inline(always)]
    pub fn calculate_stored_size(data_len: usize) -> usize {
        u64_align!(STORE_META_OVERHEAD + data_len)
    }
```

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

**File:** accounts-db/src/account_storage_reader.rs (L139-187)
```rust
    pub fn len(&self) -> usize {
        self.num_alive_bytes
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

impl<'a, R: FileBufRead<'a>> Read for AccountStorageReader<'_, R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let mut total_read = 0;
        let buf_len = buf.len();

        while total_read < buf_len {
            let next_excluded_account = self.sorted_excluded_accounts.last();
            let file_offset = self.reader.get_file_offset() as usize;
            if let Some(&(excluded_start, excluded_size)) = next_excluded_account
                && file_offset == excluded_start
            {
                let skip_len = excluded_size.min(self.num_total_bytes - excluded_start);
                self.reader.consume_or_skip(skip_len);
                self.sorted_excluded_accounts.pop();
                continue;
            }

            // Cannot read beyond the end of the buffer
            let bytes_left_in_buffer = buf_len.saturating_sub(total_read);

            // Cannot read beyond the next excluded account or the end of the file
            let bytes_to_read_from_file = if let Some((excluded_start, _)) = next_excluded_account {
                excluded_start.saturating_sub(file_offset)
            } else {
                self.num_total_bytes.saturating_sub(file_offset)
            };

            let bytes_to_read = bytes_left_in_buffer.min(bytes_to_read_from_file);

            let read_size = self.reader.read(&mut buf[total_read..][..bytes_to_read])?;

            if read_size == 0 {
                break; // EOF
            }

            total_read += read_size;
        }

        Ok(total_read)
    }
```

**File:** accounts-db/src/account_storage_reader.rs (L359-365)
```rust
        let mut number_of_accounts_to_remove = num_obsolete;
        let mut current_len = storage.accounts.len() - storage.get_obsolete_bytes(None);
        if tombstones_filter == TombstonesFilter::Exclude {
            number_of_accounts_to_remove += num_tombstones;
            current_len -= num_tombstones * storage.accounts.calculate_stored_size(0);
        }
        assert_eq!(reader.len(), current_len);
```

**File:** accounts-db/src/account_storage_entry.rs (L48-53)
```rust
    /// offsets to zero-lamport accounts that have been removed from the accounts index entirely
    /// (a tombstone — carried forward to this storage by shrink). The index has no slot_list entry
    /// pointing at them; their bytes are retained only so an incremental snapshot taken after the
    /// latest full snapshot still observes the zero-lamport account and propagates the deletion.
    /// Shrink uses this list to recognize tombstone entries without needing to scan the index.
    tombstone_offsets: RwLock<IntSet<Offset>>,
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

**File:** accounts-db/src/ancient_append_vecs.rs (L162-180)
```rust
    // sort 'shrink_indexes' by most bytes saved, highest to lowest
    fn sort_shrink_indexes_by_bytes_saved(&mut self) {
        self.shrink_indexes.sort_unstable_by(|l, r| {
            let amount_shrunk = |index: &usize| {
                let item = &self.all_infos[*index];
                // alive_bytes assumes the accounts are aligned. `written_bytes` may
                // not be aligned for the last account. Therefore, we need to
                // align it.
                let aligned_written_bytes = u64_align!(item.written_bytes as usize) as u64;
                if aligned_written_bytes < item.alive_bytes {
                    // should not happen, but if it does, submit warn log it and continue
                    datapoint_warn!(
                        "aligned_written_bytes_less_than_alive_bytes",
                        ("aligned_written_bytes", aligned_written_bytes, i64),
                        ("alive_bytes", item.alive_bytes, i64)
                    );
                }
                item.written_bytes.saturating_sub(item.alive_bytes)
            };
```
