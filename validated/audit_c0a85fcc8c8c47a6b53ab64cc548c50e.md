### Title
Unbounded, never-pruned `ObsoleteAccounts` accumulator can cause byte-count underflow / snapshot corruption in `AccountStorageReader::new` - ([File: accounts-db/src/obsolete_accounts.rs], [File: accounts-db/src/account_storage_reader.rs])

### Summary
The external report describes a class of bug where a per-cycle accounting counter (`totalWithdrawalRequests`) is incremented every period but never reset, so it accumulates indefinitely until it exceeds the bounded quantity it is subtracted from, causing an underflow revert that blocks the process (rebalancing) indefinitely. The closest reachable analog in `agave`'s AccountsDB is `ObsoleteAccounts`, the per-`AccountStorageEntry` structure used by clean/shrink to mark bytes within a storage as dead so they are skipped when snapshotting. Its `mark_accounts_obsolete` only ever appends entries and is never pruned/compacted for the lifetime of the storage, and its consumer, `AccountStorageReader::new`, subtracts the *sum of all historically recorded entries* from the storage's fixed total byte length using plain (non-saturating) arithmetic.

### Finding Description
`ObsoleteAccounts::mark_accounts_obsolete` unconditionally pushes new `(offset, data_len, slot)` entries into its internal `Vec` with no deduplication and no mechanism to prune entries once they are consumed: [1](#0-0) 

This vector lives for the entire lifetime of the `AccountStorageEntry` (an append-only region), stored as `obsolete_accounts: RwLock<ObsoleteAccounts>`: [2](#0-1) 

`get_obsolete_bytes` sums the *stored size* of every entry ever pushed (filtered only by slot, never deduplicated by offset): [3](#0-2) 

`mark_accounts_obsolete` is called from multiple independent code paths against the same storage over its lifetime: from `remove_dead_accounts`/`handle_reclaims` during ordinary `clean_accounts` (every time an older version of a pubkey is reclaimed in that storage), and from `mark_obsolete_accounts_at_startup` for accounts found to be duplicates during index generation, in addition to comments in the code acknowledging that the same physical account can be marked "dead" via more than one code path in some race windows (the comment on `zero_lamport_single_ref_offsets` at lines 36-46 explicitly documents an analogous double-accounting scenario for `num_alive_bytes`/ZLSR accounting, showing the developers are aware this kind of double-marking can occur in this subsystem). Because entries are never removed from `ObsoleteAccounts.accounts`, if the same offset is ever marked obsolete more than once (e.g., due to two independent code paths racing on the same storage, or a reclaim being visited twice across a startup + subsequent clean pass), its stored size is double-counted forever.

This accumulated (and potentially inflated) byte total is then subtracted, with plain arithmetic, from the storage's fixed total length in `AccountStorageReader::new`, which is invoked whenever a snapshot is archived/serialized for that storage: [4](#0-3) 

Because `num_total_bytes` for a given storage is fixed (an append-only file that is never appended to again once rooted, only rewritten wholesale by shrink into a *new* storage id), while `get_obsolete_bytes` grows unbounded as more (possibly duplicate) obsolete marks accumulate, the subtraction `num_total_bytes - storage.get_obsolete_bytes(snapshot_slot)` can eventually underflow. This mirrors the root cause of the Sherlock report precisely: an accounting field intended to represent a bounded "current" subset of a total is instead accumulated indefinitely across cycles/operations without ever being pruned, and is later subtracted from a bounded quantity.

### Impact Explanation
If `get_obsolete_bytes` ever exceeds `num_total_bytes` for a storage being serialized:
- In debug builds, `num_total_bytes - storage.get_obsolete_bytes(...)` panics on subtraction overflow, crashing the node during snapshot archival/serialization — a validator/node panic.
- In release builds (where overflow checks are typically disabled), the subtraction silently wraps to a huge `usize`, corrupting `num_alive_bytes`/`len()` used to size reads for the snapshot writer. This can cause an honest-node snapshot to diverge from what replay would produce (reading far more or fewer bytes than intended), i.e., a snapshot-vs-replay mismatch, or a downstream panic when consumers try to read past the buffer/file length.

This directly affects snapshot generation, a component explicitly in scope.

### Likelihood Explanation
Exploitability requires no privileged/validator-only action — it is purely a function of the storage's own internal lifecycle (clean/shrink/reclaim cadence) and is triggered by unprivileged transaction activity that produces enough account rewrites/duplicate reclaims against a long-lived storage (e.g., ancient append vecs that persist for a long time without being shrunk/replaced). The likelihood of a double-mark occurring depends on races between the startup duplicate-marking path and the ordinary clean path, or between concurrent reclaim call sites acting on the same storage — this was not something I could fully confirm is reachable in a single call path within the remaining investigation time; I could not exhaustively verify a concrete double-marking call sequence, only that (a) no deduplication guard exists anywhere in `ObsoleteAccounts`, (b) multiple independent call sites exist that can mark accounts obsolete against the same storage over its lifetime, and (c) the codebase's own comments acknowledge analogous double-accounting is possible in the neighboring ZLSR bookkeeping. This uncertainty should be resolved by a Devin session with the ability to run the full test suite and construct a reproduction.

### Recommendation
- Deduplicate offsets in `ObsoleteAccounts::mark_accounts_obsolete` (e.g., track a set of already-obsolete offsets and skip re-adding one that is already present) so the same physical bytes are never counted twice.
- Make the subtraction in `AccountStorageReader::new` (`num_total_bytes - storage.get_obsolete_bytes(snapshot_slot)`, and the tombstone-byte subtraction on the following lines) saturating (`saturating_sub`) as a defense-in-depth measure, and assert/log if `get_obsolete_bytes` would exceed `num_total_bytes` so the invariant violation is caught rather than silently corrupting snapshot output.
- Audit all call sites of `mark_accounts_obsolete` (`accounts_db.rs`, `ancient_append_vecs.rs`, `mark_obsolete_accounts_at_startup`) to confirm no code path can mark the same `(storage, offset)` obsolete twice, particularly across the startup-duplicate-detection path and the ordinary background `clean_accounts` path operating on the same rehydrated storage.

### Proof of Concept
Not independently reproduced within the scope of this investigation; a concrete PoC would need to: (1) create a storage, (2) mark the same offset obsolete via two independent call paths (e.g., once through `mark_obsolete_accounts_at_startup`'s duplicate-reclaim path and once through a subsequent normal `clean_accounts`/`handle_reclaims` reclaim of the same slot/offset), and (3) call `AccountStorageReader::new` on that storage and observe the subtraction underflow/panic (debug build) or corrupted `num_alive_bytes` (release build). This should be validated with a Devin session that can build and run the accounts-db test suite to confirm reachability of the double-mark condition.

### Citations

**File:** accounts-db/src/obsolete_accounts.rs (L18-34)
```rust
impl ObsoleteAccounts {
    /// Marks the accounts at the given offsets as obsolete
    pub fn mark_accounts_obsolete(
        &mut self,
        newly_obsolete_accounts: impl ExactSizeIterator<Item = (Offset, usize)>,
        slot: Slot,
    ) {
        self.accounts.reserve(newly_obsolete_accounts.len());

        for (offset, data_len) in newly_obsolete_accounts {
            self.accounts.push(ObsoleteAccountItem {
                offset,
                data_len,
                slot,
            });
        }
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L55-64)
```rust
    /// Obsolete Accounts. These are accounts that are still present in the storage
    /// but should be ignored during rebuild. They have been removed
    /// from the accounts index, so they will not be picked up by scan.
    /// Slot is the slot at which the account is no longer needed.
    /// Two scenarios cause an account entry to be marked obsolete
    /// 1. The account was rewritten to a newer slot
    /// 2. The account was set to zero lamports and is older than the last
    ///    full snapshot. In this case, slot is set to the snapshot slot
    pub(crate) obsolete_accounts: RwLock<ObsoleteAccounts>,
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
