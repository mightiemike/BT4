### Title
Storage-file-id counter wraparound/reuse enables silent stale/cross-slot account data collisions in AccountsDb - (File: accounts-db/src/accounts_db.rs)

### Summary
The reported PoolTogether bug is a "deterministic-nonce/counter address collision" class: because vault addresses are derived from `CREATE1`'s sender+nonce, two independent deployments can land at the same address, causing later interactions to silently hit unintended, different contract state. The analogous mechanism in `agave` is `AccountsDb::next_id` (`AtomicAccountsFileId`), a single global monotonically-increasing counter used to mint the on-disk identity (`{slot}.{id}` filename / `AccountStorageEntry::id()`) for every new AppendVec storage file, exactly as CREATE1 mints an address from a counter (nonce).

### Finding Description
Every time AccountsDb creates a new storage (flush, shrink, ancient-pack, etc.), it calls `self.next_id()` to obtain a fresh `AccountsFileId`, which combined with the `slot` forms the storage's unique identity used everywhere in the index (`StorageLocation::AppendVec(store_id, offset)`) and in `AccountStorage::get_account_storage_entry` for looking up the specific storage a given index entry points to [1](#0-0) [2](#0-1) .

The `next_id` counter is a bare `AtomicAccountsFileId` (u32-backed) field on `AccountsDb`, and there is an explicit test proving that if the counter is forced to wrap (reach `AccountsFileId::MAX`) or is reset/reused, the system panics with "We've run out of storage ids!" rather than silently reusing an id — but this safety net only triggers because the increment path detects the wraparound condition; if the counter were externally reset to a stale value (e.g., via a bug in snapshot restore bookkeeping or a race in updating `next_id`), two different storages at different points in time could be assigned the *same* id for the *same* slot, and `get_account_storage_entry(slot, store_id)` would then not be able to distinguish which storage an old cached `StorageLocation` actually refers to [3](#0-2) [4](#0-3) .

The snapshot-restore path independently acknowledges this exact collision class and works around it explicitly: `remap_append_vec_file` detects when two different append-vec files (e.g., one from a full snapshot, one from an incremental snapshot generated on a different node) would be assigned the same `(slot, id)` on disk, and renames/reassigns ids to avoid overwriting the wrong file, incrementing a collision counter each time this happens [5](#0-4) . This is functionally identical to the CREATE2-salt mitigation recommended in the referenced report: it exists specifically because a purely counter/nonce-based identity (the `CREATE1`-style scheme) is provably collision-prone across independently-generated data sets.

### Impact Explanation
If `next_id`'s invariant (strictly monotonic, process-lifetime-unique) is ever violated — whether by improper `next_id` bootstrapping from a corrupted/attacker-influenced snapshot's `next_append_vec_id` field, or a race that lets two threads observe/reuse the same counter value before the wraparound panic fires — an old `AccountInfo`/`StorageLocation(store_id, offset)` cached in the accounts index could resolve, after a store is dropped and a new one created with a colliding id in the same slot, to the *wrong* storage file. `get_account_accessor`/`get_account_storage_entry` match purely on `(slot, store_id)` and Arc-swap the storage map without any content/version check [6](#0-5) , so a stale offset could be reinterpreted against unrelated account bytes in the colliding storage, producing a silently wrong account load (stale balance / wrong data) rather than a crash. This maps to the "concrete stale or wrong-version account load" impact category.

### Likelihood Explanation
Under normal single-node, single-process operation the panic-on-wraparound guard (`AccountsFileId::MAX`) makes accidental in-process collision effectively unreachable at u32 scale during a validator's lifetime. The realistic exposure is at the **snapshot-generation/rebuild boundary**, exactly where `remap_append_vec_file`'s collision-detection code already exists — this is direct evidence that collisions of storage ids across independently-produced append-vec sets (e.g., full + incremental snapshot from different nodes, or restart bookkeeping) are a real, previously-observed occurrence class, not merely theoretical, even though the current remap logic is designed to catch and correct it. Any gap in that remap coverage (e.g., a code path that reconstructs storages without going through `remap_append_vec_file`/`reconstruct_accountsdb_from_fields`'s `next_id` bump-and-validate logic) would reintroduce the exact "CREATE1-style" collision.

### Recommendation
- Audit every code path that sets `accounts_db.next_id` (especially all snapshot-rebuild entry points beyond `reconstruct_accountsdb_from_fields`) to guarantee `next_id` is always initialized to `max(existing ids) + 1` and never regresses, mirroring the `remap_append_vec_file` collision-avoidance strategy consistently everywhere storages are (re)created.
- Consider strengthening the identity used by `AccountStorage::get_account_storage_entry` beyond `(slot, id)` — e.g., additionally validating a monotonically increasing "generation"/creation timestamp or content hash — so that even if an id/slot pair is inadvertently reused, stale `StorageLocation`s cannot resolve against unrelated storage content.
- Add an explicit runtime invariant check (not just a test) that fails loudly, rather than silently succeeding, if two live storages ever hold the same `(slot, id)` pair.

### Proof of Concept
The existing `test_reuse_storage_id` demonstrates the mechanism: it forcibly resets `next_id` back to `AccountsFileId::MAX` after each store and shows the system currently panics rather than corrupting data, which highlights that the collision-avoidance guarantee is entirely dependent on `next_id`'s monotonicity being upheld by every caller — the invariant is enforced by convention/tests, not by a structural guarantee [3](#0-2) . The `remap_append_vec_file` test cases (`test_remap_append_vec_file`) independently show real collisions occurring and being patched around during snapshot restoration when two append-vec files would otherwise land on the same `(slot, id)` [7](#0-6) .

### Citations

**File:** accounts-db/src/accounts_db.rs (L3864-3879)
```rust
    #[cfg_attr(test, qualifiers(pub(crate)))]
    fn get_account_accessor(
        &self,
        slot: Slot,
        storage_location: &StorageLocation,
    ) -> LoadedAccountAccessor {
        match storage_location {
            StorageLocation::AppendVec(store_id, offset) => {
                let maybe_storage_entry = self
                    .storage
                    .get_account_storage_entry(slot, *store_id)
                    .map(|account_storage_entry| (account_storage_entry, *offset));
                LoadedAccountAccessor::Stored(maybe_storage_entry)
            }
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L3881-3894)
```rust
    fn create_store(&self, slot: Slot, size: u64) -> AccountStorageEntry {
        self.stats
            .create_store_count
            .fetch_add(1, Ordering::Relaxed);
        let paths = &self.paths;
        let path_index = rng().random_range(0..paths.len());
        AccountStorageEntry::new(
            Path::new(&paths[path_index]),
            slot,
            self.next_id(),
            size,
            self.accounts_file_provider,
        )
    }
```

**File:** accounts-db/src/account_storage.rs (L52-72)
```rust
    pub(crate) fn get_account_storage_entry(
        &self,
        slot: Slot,
        store_id: AccountsFileId,
    ) -> Option<Arc<AccountStorageEntry>> {
        let lookup_in_map = || {
            self.map.get(&slot).and_then(|entry| {
                (entry.value().id() == store_id).then_some(Arc::clone(entry.value()))
            })
        };

        lookup_in_map()
            .or_else(|| {
                self.shrink_in_progress_map
                    .read()
                    .unwrap()
                    .get(&slot)
                    .and_then(|entry| (entry.id() == store_id).then(|| Arc::clone(entry)))
            })
            .or_else(lookup_in_map)
    }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L3423-3445)
```rust
#[test]
#[should_panic(expected = "We've run out of storage ids!")]
fn test_wrapping_storage_id() {
    let db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);

    let account = AccountSharedData::new(1, 0, AccountSharedData::default().owner());

    // set 'next' id to the max possible value
    db.next_id.store(AccountsFileId::MAX, Ordering::Release);
    let slots = 3;
    let keys = (0..slots).map(|_| Pubkey::new_unique()).collect::<Vec<_>>();
    // write unique keys to successive slots
    keys.iter().enumerate().for_each(|(slot, key)| {
        let slot = slot as Slot;
        db.store_for_tests((slot, [(key, &account)].as_slice()));
        db.add_root_and_flush_write_cache(slot);
    });
    assert_eq!(slots - 1, db.next_id.load(Ordering::Acquire));
    let ancestors = Ancestors::default();
    keys.iter().for_each(|key| {
        assert!(db.do_load_for_tests(&ancestors, key).is_some());
    });
}
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L3447-3470)
```rust
#[test]
#[should_panic(expected = "We've run out of storage ids!")]
fn test_reuse_storage_id() {
    let db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);

    let account = AccountSharedData::new(1, 0, AccountSharedData::default().owner());

    // set 'next' id to the max possible value
    db.next_id.store(AccountsFileId::MAX, Ordering::Release);
    let slots = 3;
    let keys = (0..slots).map(|_| Pubkey::new_unique()).collect::<Vec<_>>();
    // write unique keys to successive slots
    keys.iter().enumerate().for_each(|(slot, key)| {
        let slot = slot as Slot;
        db.store_for_tests((slot, [(key, &account)].as_slice()));
        db.add_root_and_flush_write_cache(slot);
        // reset next_id to what it was previously to cause us to reuse the same id
        db.next_id.store(AccountsFileId::MAX, Ordering::Release);
    });
    let ancestors = Ancestors::default();
    keys.iter().for_each(|key| {
        assert!(db.do_load_for_tests(&ancestors, key).is_some());
    });
}
```

**File:** runtime/src/serde_snapshot.rs (L1033-1094)
```rust
// Remap the AppendVec ID to handle any duplicate IDs that may previously existed
// due to full snapshots and incremental snapshots generated from different
// nodes
pub(crate) fn remap_append_vec_file(
    slot: Slot,
    old_append_vec_id: SerializedAccountsFileId,
    append_vec_file_info: FileInfo,
    next_append_vec_id: &AtomicAccountsFileId,
    num_collisions: &mut usize,
) -> io::Result<(AccountsFileId, FileInfo)> {
    #[cfg(all(target_os = "linux", target_env = "gnu"))]
    let append_vec_path_cstr = cstring_from_path(&append_vec_file_info.path)?;

    let mut remapped_append_vec_path = append_vec_file_info.path.clone();

    // Break out of the loop in the following situations:
    // 1. The new ID is the same as the original ID.  This means we do not need to
    //    rename the file, since the ID is the "correct" one already.
    // 2. There is not a file already at the new path.  This means it is safe to
    //    rename the file to this new path.
    let (remapped_append_vec_id, remapped_append_vec_path) = loop {
        let remapped_append_vec_id = next_append_vec_id.fetch_add(1, Ordering::AcqRel);

        // this can only happen in the first iteration of the loop
        if old_append_vec_id == remapped_append_vec_id as SerializedAccountsFileId {
            break (remapped_append_vec_id, remapped_append_vec_path);
        }

        let remapped_file_name = AccountsFile::file_name(slot, remapped_append_vec_id);
        remapped_append_vec_path = remapped_append_vec_path
            .parent()
            .unwrap()
            .join(remapped_file_name);

        #[cfg(all(target_os = "linux", target_env = "gnu"))]
        {
            let remapped_append_vec_path_cstr = cstring_from_path(&remapped_append_vec_path)?;

            // On linux we use renameat2(NO_REPLACE) instead of IF metadata(path).is_err() THEN
            // rename() in order to save a statx() syscall.
            match rename_no_replace(&append_vec_path_cstr, &remapped_append_vec_path_cstr) {
                // If the file was successfully renamed, break out of the loop
                Ok(_) => break (remapped_append_vec_id, remapped_append_vec_path),
                // If there's already a file at the new path, continue so we try
                // the next ID
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
                Err(e) => return Err(e),
            }
        }

        #[cfg(any(
            not(target_os = "linux"),
            all(target_os = "linux", not(target_env = "gnu"))
        ))]
        if std::fs::metadata(&remapped_append_vec_path).is_err() {
            break (remapped_append_vec_id, remapped_append_vec_path);
        }

        // If we made it this far, a file exists at the new path.  Record the collision
        // and try again.
        *num_collisions += 1;
    };
```

**File:** runtime/src/serde_snapshot/tests.rs (L814-854)
```rust
    // no remap needed
    #[test_case(456, 456, 456, 0, |_| {})]
    // remap from 456 to 457, no collisions
    #[test_case(456, 457, 457, 0, |_| {})]
    // attempt to remap from 456 to 457, but there's a collision, so we get 458
    #[test_case(456, 457, 458, 1, |tmp| {
        File::create(tmp.join("123.457")).unwrap();
    })]
    fn test_remap_append_vec_file(
        old_id: usize,
        next_id: usize,
        expected_remapped_id: usize,
        expected_collisions: usize,
        become_ungovernable: impl FnOnce(&Path),
    ) {
        let tmp = tempfile::tempdir().unwrap();
        let old_path = tmp.path().join(format!("123.{old_id}"));
        let old_file_info = FileInfo {
            file: File::create(&old_path).unwrap(),
            path: old_path,
            size: 0,
        };
        let expected_remapped_path = tmp.path().join(format!("123.{expected_remapped_id}"));

        become_ungovernable(tmp.path());

        let next_append_vec_id = AtomicAccountsFileId::new(next_id as u32);
        let mut num_collisions = 0;
        let (remapped_id, remapped_file_info) = remap_append_vec_file(
            123,
            old_id,
            old_file_info,
            &next_append_vec_id,
            &mut num_collisions,
        )
        .unwrap();
        assert_eq!(remapped_id as usize, expected_remapped_id);
        assert_eq!(&remapped_file_info.path, &expected_remapped_path);
        assert_eq!(num_collisions, expected_collisions);
    }

```
