### Title
Unchecked subtraction can underflow `AccountStorageReader::num_alive_bytes` when tombstone bytes are double-counted against obsolete bytes - ([File: accounts-db/src/account_storage_reader.rs])

### Summary
`AccountStorageReader::new` computes the number of "alive" bytes to read from an account storage entry by starting from the file's total length and subtracting obsolete bytes and (optionally) tombstone bytes, using plain unchecked `usize` subtraction rather than a checked/saturating operation. This mirrors the `PerpDepository.netAssetDeposits` pattern in the referenced report: a running total is decremented by amounts derived from independently-tracked accounting structures, and if those structures ever overlap or diverge from the base total, the subtraction underflows.

### Finding Description
`AccountStorageReader::new` computes: [1](#0-0) 
then, when tombstones should be excluded, further subtracts tombstone bytes from the already-obsolete-adjusted total: [2](#0-1) 

Both `storage.get_obsolete_bytes(snapshot_slot)` and `tombstone_offsets.len() * tombstone_stored_size` are computed independently, from two different bookkeeping structures on `AccountStorageEntry`: `obsolete_accounts` (a `Vec<ObsoleteAccountItem>` recorded whenever an account is rewritten or zero-lamported) and `tombstone_offsets` (an `IntSet<Offset>` populated when shrink carries forward zero-lamport accounts as tombstones): [3](#0-2) 

`get_obsolete_bytes` itself iterates `obsolete_accounts` and sums stored sizes with no knowledge of `tombstone_offsets`: [4](#0-3) 

There is no assertion or invariant enforced anywhere in this code path (unlike the sibling accounting path `AccountStorageEntry::remove_accounts`, which does defensively assert before subtracting): [5](#0-4) 

If a tombstone's offset is ever also reachable through `obsolete_accounts` filtering for the same `snapshot_slot` (i.e., its bytes get counted once via `get_obsolete_bytes` and a second time via the tombstone-specific subtraction), or more generally if the two independently-maintained sets of "non-alive" bytes together exceed `num_total_bytes`, the plain `-=` at line 101 or line 120 underflows. In a release build without overflow checks this silently wraps to a huge `usize`, and `AccountStorageReader::len()`/`is_empty()` (used to size buffers and iterate the storage when generating snapshots) would then return a bogus huge value. [6](#0-5) 

This is structurally the same bug class as the Sherlock M-12 report: a size/count tracker (`netAssetDeposits` there, `num_alive_bytes` here) is derived by subtracting values sourced from two logically-related but independently updated data structures (deposit vs. withdrawal amounts there; obsolete-accounts bytes vs. tombstone bytes here), with no defensive check that the subtraction cannot exceed the base value.

### Impact Explanation
If triggered, this would corrupt the computed "alive bytes" count used when reading/serializing an account storage entry for a snapshot. In a debug build (overflow-checks enabled, as used in CI/tests) this manifests as an immediate panic — a node crash / DoS during snapshot generation. In a release build it silently wraps to a near-`usize::MAX` value, which would be used downstream to size reads/writes for the storage file, risking either a snapshot-vs-replay/hash mismatch (corrupted snapshot content) or a crash from an out-of-bounds read attempt. Either failure mode falls squarely within the categories called out as in-scope (snapshot generation and rebuild; node panic; honest-node snapshot mismatch).

### Likelihood Explanation
I could not fully verify, given the tool budget, whether the code paths that populate `tombstone_offsets` (via `store_tombstones`/shrink) and `obsolete_accounts` (via `mark_accounts_obsolete`) can currently produce overlapping offsets for the same storage entry under the invariants the codebase currently maintains elsewhere (e.g., whether a tombstone offset is always excluded from being additionally marked obsolete). The subtraction is unconditionally unchecked in both places I found, so the arithmetic is unsafe by construction regardless of whether a current call path can reach the overlapping state — but I was not able to construct or confirm a concrete reachable trigger within the remaining investigation budget. This should be treated as a real code smell/latent bug requiring further confirmation of the surrounding invariants (in the same spirit as the Sherlock issue being accepted only as a medium/edge-case finding), rather than a proven, immediately exploitable path.

### Recommendation
- Replace the unchecked subtractions in `AccountStorageReader::new` with `checked_sub` (returning an `io::Error` on failure) or, at minimum, `saturating_sub` plus a `debug_assert!`/metric to detect the invariant violation instead of silently corrupting the byte count.
- Audit `mark_accounts_obsolete` and the tombstone-carry-forward path in shrink to guarantee, and ideally assert, that `obsolete_accounts` and `tombstone_offsets` offset sets are disjoint for a given storage entry and slot filter, mirroring the explicit invariant check already present in `AccountStorageEntry::remove_accounts`.

### Proof of Concept
Not constructed — the finding is inferred purely from code-level analysis of the arithmetic and data structures; I did not verify a concrete sequence of clean/shrink/snapshot operations that produces overlapping obsolete/tombstone offsets for the same storage entry, and no such test currently exercises this combination in the codebase (based on the tests I inspected in `accounts-db/src/accounts_db/tests/impl.rs`, which test `alive_bytes`/`alive_bytes_after_shrink`/tombstone accounting separately, not their combination through `AccountStorageReader`).

### Citations

**File:** accounts-db/src/account_storage_reader.rs (L100-101)
```rust
        let num_total_bytes = storage.accounts.len();
        let mut num_alive_bytes = num_total_bytes - storage.get_obsolete_bytes(snapshot_slot);
```

**File:** accounts-db/src/account_storage_reader.rs (L115-126)
```rust
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

**File:** accounts-db/src/account_storage_entry.rs (L194-213)
```rust
    /// Batch-insert tombstone offsets, taking the offsets lock once.
    /// Returns the number of offsets inserted.
    pub(crate) fn batch_insert_tombstone_offsets(
        &self,
        offsets: impl IntoIterator<Item = Offset>,
    ) -> usize {
        let mut tombstone_offsets = self.tombstone_offsets.write().unwrap();
        let mut num_inserted = 0;
        for offset in offsets {
            if tombstone_offsets.insert(offset) {
                num_inserted += 1;
            }
        }
        num_inserted
    }

    /// Locks the tombstone offset set with a read lock and returns it with the guard.
    pub(crate) fn tombstone_offsets_read_lock(&self) -> RwLockReadGuard<'_, IntSet<Offset>> {
        self.tombstone_offsets.read().unwrap()
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
