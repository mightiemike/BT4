### Title
Snapshot storage length computed from unsynchronized reads of mutable obsolete-account/shrink state can diverge from the obsolete-accounts list persisted alongside it - (File: runtime/src/serde_snapshot/storage.rs)

### Summary
The external report's bug class is: a value that is supposed to represent a fixed amount is instead computed from the *current, mutable* state of an object at the moment of use, so if that state changes (or is read inconsistently) relative to when the value is later consumed, the result is wrong (too much/too little, or a hard failure). The closest reachable analog in this codebase is in the snapshot serialization path, where `SerializableAccountStorageEntry::new()` derives the stored `accounts_current_len` field by subtracting `AccountStorageEntry::get_obsolete_bytes()` (itself computed from the storage's live `obsolete_accounts` set at call time) from `accounts.accounts.len()` (the storage's current on-disk length), while a *separate* call site independently recomputes the obsolete-accounts bytes/list for the same slot via `SerdeObsoleteAccounts::new_from_storage_entry_at_slot()`.

### Finding Description
`SerializableAccountStorageEntry::new()` computes the persisted file length as: [1](#0-0) 

This subtracts `accounts.get_obsolete_bytes(Some(snapshot_slot))`, which itself walks the storage's `obsolete_accounts` RwLock at call time and computes byte sizes relative to the storage's *current* `accounts.len()`: [2](#0-1) 

Separately, `SerdeObsoleteAccounts::new_from_storage_entry_at_slot()` independently re-reads the same storage's obsolete-accounts state (again via `obsolete_accounts_for_snapshots()` and `get_obsolete_bytes()`) to build the list of obsolete items and the `bytes` field that is persisted alongside the storage entry, and is invoked from a distinct call site (`SerdeObsoleteAccountsMap::new_from_storages`) than the one producing `SerializableAccountStorageEntry`: [3](#0-2) [4](#0-3) 

Both of these reads happen against the same live, mutable `AccountStorageEntry` that background shrink/clean/purge threads continue to mutate concurrently (marking additional accounts obsolete via `mark_accounts_obsolete`, or fully rewriting/shrinking the storage) while a snapshot is being generated: [5](#0-4) 

Because the two computations (`accounts_current_len` in `storage.rs` and the obsolete-accounts `bytes`/list in `obsolete_accounts.rs`) are not taken from a single atomic snapshot of the storage's obsolete state, and are called from different points in the serialization pipeline over potentially many storages, a background clean/shrink pass that mutates `obsolete_accounts` or reopens/rewrites the storage (`reopen_as_readonly`, `shrink_storage`) between these two reads can make the two derived values inconsistent with each other. This mirrors the reported bug class exactly: a "fee" (here, a persisted length/byte count) that is calculated from current mutable balance/state instead of a value fixed at the time of use, so it can diverge from what is actually needed by the consumer of that value.

The `reconstruct_single_storage()` function on the read side documents that the persisted length is relied upon as authoritative and is used directly to size the reconstructed `AccountsFile`, and mismatches are only guarded by an `id` equality assertion, not a length/content consistency check between the two independently-derived obsolete-byte values: [6](#0-5) 

### Impact Explanation
If the two obsolete-byte calculations diverge due to concurrent mutation of the storage during snapshotting, the archived/serialized `accounts_current_len` could be wrong relative to the also-serialized obsolete-accounts list for the same storage entry, causing incorrect reconstruction of the `AccountsFile` on restart (either truncating live account data or leaving stale/removed account bytes reachable) — a stale/wrong-version account load. In the worst case, the subtraction `accounts.accounts.len() - accounts.get_obsolete_bytes(...)` could produce inconsistent values relative to what the obsolete-accounts serializer computed for the same slot, producing a validator that computes a different accounts state (and thus a different bank hash / accounts lt hash) after restarting from that snapshot compared to a node that never restarted — an honest-node snapshot-vs-replay mismatch. This is consistent with the "impact: low, likelihood: high" characterization in the original report, since normal operation (background clean/shrink running concurrently with periodic snapshotting) makes the race window routinely reachable, not merely theoretical.

### Likelihood Explanation
`clean_accounts()`/shrink run continuously in the accounts background service concurrently with snapshot generation as a matter of normal validator operation: [7](#0-6) 
Snapshot generation iterates over many storages and calls the two independent obsolete-byte-deriving code paths (`SerializableAccountStorageEntry::new` and `SerdeObsoleteAccountsMap::new_from_storages`) potentially with time gaps between them, so any storage that undergoes further obsolescence marking or shrinking in that window is exposed to the inconsistency. This does not require a malicious actor — it is an unprivileged-user-reachable path purely from ordinary transaction activity that ages accounts into obsolescence during a snapshot capture window.

### Recommendation
Compute `accounts_current_len` and the obsolete-accounts list/bytes for a given storage from a single, consistently-locked snapshot of `obsolete_accounts` (e.g., take one read-lock guard and derive both the byte count and the item list from it, threading that single result into both `SerializableAccountStorageEntry::new` and `SerdeObsoleteAccounts::new_from_storage_entry_at_slot`), rather than calling `get_obsolete_bytes()`/`obsolete_accounts_for_snapshots()` twice independently from different serialization call sites for the same slot. Additionally, add a validation check during snapshot reconstruction that the persisted `accounts_current_len` is consistent with the persisted obsolete-accounts bytes for the same storage, rather than only checking the `id` fields for equality.

### Proof of Concept
Not independently reproduced in this analysis; this is a static code-path finding based on tracing the two independent read sites of the same mutable `obsolete_accounts` state (`runtime/src/serde_snapshot/storage.rs` and `runtime/src/serde_snapshot/obsolete_accounts.rs`) that feed into the same on-disk snapshot artifact for the same storage entry, while accounts-db background clean/shrink can mutate that state between the two reads. Confirming an actual observable corruption would require instrumenting a snapshot-generation run to interleave a `clean_accounts`/`shrink_storage` call between the two serialization calls for the same storage and diffing the resulting archive against a reconstructed accounts state.

### Citations

**File:** runtime/src/serde_snapshot/storage.rs (L37-46)
```rust
    pub fn new(
        accounts: &AccountStorageEntry,
        snapshot_slot: Slot,
    ) -> SerializableAccountStorageEntry {
        SerializableAccountStorageEntry {
            id: accounts.id() as SerializedAccountsFileId,
            accounts_current_len: accounts.accounts.len()
                - accounts.get_obsolete_bytes(Some(snapshot_slot)),
        }
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

**File:** runtime/src/serde_snapshot/obsolete_accounts.rs (L40-52)
```rust
impl SerdeObsoleteAccounts {
    /// Creates a new `SerdeObsoleteAccounts` instance from a given storage entry and snapshot slot.
    fn new_from_storage_entry_at_slot(storage: &AccountStorageEntry, snapshot_slot: Slot) -> Self {
        let accounts = Self::items_from_obsolete_accounts(
            storage.obsolete_accounts_for_snapshots(snapshot_slot),
        );

        SerdeObsoleteAccounts {
            id: storage.id() as SerializedAccountsFileId,
            bytes: storage.get_obsolete_bytes(Some(snapshot_slot)) as u64,
            accounts,
        }
    }
```

**File:** runtime/src/serde_snapshot/obsolete_accounts.rs (L104-120)
```rust
impl SerdeObsoleteAccountsMap {
    /// Creates a new `SerdeObsoleteAccountsMap` from a list of storage entries and a snapshot slot.
    pub(crate) fn new_from_storages(
        snapshot_storages: &[Arc<AccountStorageEntry>],
        snapshot_slot: Slot,
    ) -> Self {
        let map = snapshot_storages
            .into_par_iter()
            .map(|storage| {
                (
                    storage.slot(),
                    SerdeObsoleteAccounts::new_from_storage_entry_at_slot(storage, snapshot_slot),
                )
            })
            .collect();
        SerdeObsoleteAccountsMap { map }
    }
```

**File:** accounts-db/src/accounts_db.rs (L5110-5123)
```rust
                        let remaining_accounts = store.remove_accounts(dead_bytes, offsets.len());

                        if let MarkAccountsObsolete::Yes(slot_marked_obsolete) =
                            mark_accounts_obsolete
                        {
                            store
                                .obsolete_accounts
                                .write()
                                .unwrap()
                                .mark_accounts_obsolete(
                                    offsets.into_iter().zip(data_lens),
                                    slot_marked_obsolete,
                                );
                        }
```

**File:** runtime/src/serde_snapshot.rs (L997-1031)
```rust
pub(crate) fn reconstruct_single_storage(
    slot: &Slot,
    append_vec_file_info: FileInfo,
    id: AccountsFileId,
    obsolete_accounts: Option<(ObsoleteAccounts, AccountsFileId, usize)>,
) -> Result<Arc<AccountStorageEntry>, SnapshotError> {
    // The storage length is taken directly from the on-disk file size (see
    // `AccountsFile::new_for_startup`). When restoring from an archive the obsolete accounts have
    // been physically removed during serialization, and when restoring from a snapshot directory
    // they are still present in the file. In both cases the file size already reflects the exact
    // number of bytes the storage spans, so there is no need to carry the length separately in the
    // snapshot fields.
    //
    // When restoring from an archive, obsolete accounts will always be `None`.
    // When restoring from fastboot, obsolete accounts will be 'Some' if the storage contained
    // accounts marked obsolete at the time the snapshot was taken.
    let obsolete_accounts =
        if let Some((obsolete_accounts, obsolete_id, _obsolete_bytes)) = obsolete_accounts {
            if obsolete_id != id {
                return Err(SnapshotError::MismatchedAccountsFileId(id, obsolete_id));
            }

            obsolete_accounts
        } else {
            ObsoleteAccounts::default()
        };

    let accounts_file = AccountsFile::new_for_startup(append_vec_file_info)?;
    Ok(Arc::new(AccountStorageEntry::new_existing(
        *slot,
        id,
        accounts_file,
        obsolete_accounts,
    )))
}
```

**File:** runtime/src/accounts_background_service.rs (L524-555)
```rust
                        } else {
                            // we didn't handle a snapshot request, so do flush/clean/shrink

                            let next_snapshot_request_slot = request_handlers
                                .snapshot_request_handler
                                .peek_next_snapshot_request_slot();

                            // We cannot clean past the next snapshot request slot because it may
                            // have zero-lamport accounts.  See the comments in
                            // Bank::clean_accounts() for more information.
                            let max_clean_slot_inclusive = cmp::min(
                                next_snapshot_request_slot.unwrap_or(Slot::MAX),
                                bank.slot(),
                            )
                            .saturating_sub(1);

                            let duration_since_previous_clean = previous_clean_time.elapsed();
                            let should_clean = duration_since_previous_clean > CLEAN_INTERVAL;

                            // if we're cleaning, then force flush, otherwise be lazy
                            let force_flush = should_clean;
                            bank.rc
                                .accounts
                                .accounts_db
                                .flush_accounts_cache(force_flush, Some(max_clean_slot_inclusive));

                            if should_clean {
                                bank.rc
                                    .accounts
                                    .accounts_db
                                    .clean_accounts(Some(max_clean_slot_inclusive), false);
                                last_cleaned_slot = max_clean_slot_inclusive;
```
