### Title
Underflow panic in `SerializableAccountStorageEntry::new` during snapshot serialization - (File: `runtime/src/serde_snapshot/storage.rs`)

### Summary
`get_obsolete_bytes` sums, per obsolete account, `min(calculate_stored_size(data_len), self.accounts.len() - offset)` — a plain (non-saturating) subtraction of `offset` from the storage's current length [1](#0-0) . This value is then subtracted from `accounts.len()` again in `SerializableAccountStorageEntry::new` using plain subtraction, with no `checked_sub`/`saturating_sub` guard [2](#0-1) . This mirrors the reported bug class: an unguarded subtraction of two runtime-derived quantities that can underflow and panic, unlike the analogous read path in `AccountStorageReader::new`, which performs the same subtraction pattern [3](#0-2) .

### Finding Description
`AccountStorageEntry::get_obsolete_bytes` computes, for each obsolete account record, `self.accounts.calculate_stored_size(data_len).min(self.accounts.len() - offset)` [1](#0-0) . If `offset` for any obsolete account item is greater than `self.accounts.len()` (the storage's *current* accounts length), `self.accounts.len() - offset` underflows `usize` and panics in debug builds (or wraps to a huge number in release, corrupting `.min()`'s result and thus `obsolete_bytes`).

`ObsoleteAccountItem::offset` records the byte offset within the storage at the time the account was marked obsolete [4](#0-3) . `filter_obsolete_accounts` only filters by `slot`, not by validity relative to the storage's current length [5](#0-4) .

The result, `obsolete_bytes`, then flows into `SerializableAccountStorageEntry::new`, used when writing full/incremental snapshots:
```
accounts_current_len: accounts.accounts.len() - accounts.get_obsolete_bytes(Some(snapshot_slot))
``` [2](#0-1) 
This is a second unguarded subtraction: if `get_obsolete_bytes` (correctly or due to the inner underflow) returns a value larger than `accounts.accounts.len()`, this line panics with an arithmetic-underflow error. Note the contrast with the read-path equivalent `AccountStorageReader::new`, which performs the identical subtraction (`num_total_bytes - storage.get_obsolete_bytes(...)` and `num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size`) also without saturating guards [3](#0-2) , showing this arithmetic pattern is used in multiple places without defensive checks, unlike other parts of the codebase (e.g., `shrink_collect`'s `bytes_removed` calculation which deliberately uses `saturating_sub` [6](#0-5) , or `alive_bytes_exclude_zero_lamport_single_ref_accounts` which also uses `saturating_sub` [7](#0-6) ).

### Impact Explanation
`SerializableAccountStorageEntry::new` is invoked on every storage entry when the validator serializes a full or incremental snapshot. A panic here would abort snapshot generation, which — if triggered on the majority of validators simultaneously (since all honest nodes would compute the same len/offset relationship from the same storage state after replay) — could cause a cluster-wide denial of service in snapshot production. Because obsolete-account bookkeeping (offsets recorded, and storage length that can later be reduced e.g. by resizing/truncation, ancient-vec squashing, etc.) is complex and evolving code, any code path that leaves `offset` values stale relative to `accounts.len()` (for example, if a storage's underlying `accounts.len()` is shrunk/replaced after obsolete offsets were recorded, or a bug in whichever mark-obsolete call inserted a wrong offset) would trigger this panic deterministically on affected validators during snapshot generation.

### Likelihood Explanation
I could not concretely prove within the code reachable in this session that `offset` can exceed `self.accounts.len()` in practice — normally obsolete accounts are recorded before any operation that would reduce `self.accounts.len()` for that same storage. This weakens confidence that the underflow is currently reachable via a valid, non-buggy call sequence. However, the code carries no defensive `checked_sub`/`saturating_sub` at either of the two chained subtraction sites, unlike sibling arithmetic in the same module (`alive_bytes_exclude_zero_lamport_single_ref_accounts`, `bytes_removed` in shrink stats) which do use saturating arithmetic. This asymmetry is exactly the bug class described in the report: an unguarded subtraction ordering/direction that is safe under current invariants but fragile to any future change (or an as-yet-unproven edge case) breaking those invariants, and any panic here is on the snapshot-generation path used by all honest nodes.

### Recommendation
- Change `get_obsolete_bytes` in `accounts-db/src/account_storage_entry.rs` to use `self.accounts.len().saturating_sub(offset)` instead of `self.accounts.len() - offset` [1](#0-0) .
- Change `SerializableAccountStorageEntry::new` in `runtime/src/serde_snapshot/storage.rs` to use `accounts.accounts.len().saturating_sub(accounts.get_obsolete_bytes(Some(snapshot_slot)))` instead of the unchecked subtraction [2](#0-1) .
- Apply the same fix to the analogous subtractions in `AccountStorageReader::new` (`num_total_bytes - storage.get_obsolete_bytes(...)` and `num_alive_bytes -=  tombstone_offsets.len() * tombstone_stored_size`) [3](#0-2)  for consistency and defense in depth, and add an assertion/test that `offset <= accounts.len()` is always maintained for obsolete-account bookkeeping.

### Proof of Concept
Not concretely reproducible with the code inspected in this session — I found no call path in the explored code that records an `ObsoleteAccountItem.offset` greater than the storage's current `accounts.len()` at serialization time, so I cannot construct a concrete trigger. This finding is reported as a code-hardening issue based on the unguarded-subtraction pattern matching the reported bug class, not as a proven exploitable panic. A background agent with fuller repo access should audit all call sites of `mark_accounts_obsolete` (including storage-shrink and squash/ancient-append-vec paths) to determine whether any resize/rewrite of a storage's `AccountsFile` can occur after obsolete offsets referencing the old, larger length have been recorded, which would be the concrete trigger for this underflow.

### Citations

**File:** accounts-db/src/account_storage_entry.rs (L150-159)
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

**File:** accounts-db/src/obsolete_accounts.rs (L1-16)
```rust
use {crate::account_info::Offset, solana_clock::Slot};

#[derive(Debug, Clone, PartialEq)]
pub struct ObsoleteAccountItem {
    /// Offset of the account in the account storage entry
    pub offset: Offset,
    /// Length of the account data
    pub data_len: usize,
    /// Slot when the account was marked obsolete
    pub slot: Slot,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct ObsoleteAccounts {
    pub accounts: Vec<ObsoleteAccountItem>,
}
```

**File:** accounts-db/src/obsolete_accounts.rs (L39-47)
```rust
    pub fn filter_obsolete_accounts(
        &self,
        slot: Option<Slot>,
    ) -> impl Iterator<Item = (Offset, usize)> + '_ {
        self.accounts
            .iter()
            .filter(move |obsolete_account| slot.is_none_or(|s| obsolete_account.slot <= s))
            .map(|obsolete_account| (obsolete_account.offset, obsolete_account.data_len))
    }
```

**File:** accounts-db/src/accounts_db.rs (L2656-2660)
```rust
        stats.bytes_removed.fetch_add(
            written_bytes
                .saturating_sub(alive_total_bytes as u64)
                .saturating_sub(shrink_collect.tombstones_total_bytes as u64),
            Ordering::Relaxed,
```
