I found a genuinely strong analog: the account-scanning loop in `AppendVec::scan_accounts_stored_meta` that determines where a stored account's data ends by looking for a "zeroed" (default pubkey + zero lamports) sentinel — structurally the same bug class as `BigNumber`'s "trim until non-zero word" loop with no bound on how far it can run relative to the intended buffer length.

### Title
Account-storage scan loop can silently stop at an attacker/state-induced all-zero record boundary, truncating index generation - (File: `accounts-db/src/append_vec.rs`)

### Summary
`AppendVec::scan_accounts_stored_meta` (used by `scan_accounts`/`scan_accounts_without_data`, which in turn drive `generate_index_for_slot`, `get_unique_accounts_from_storage[_for_shrink]`, and shrink/clean/rebuild paths) walks the storage file record-by-record and treats a record whose `lamports == 0` **and** `pubkey == Pubkey::default()` as "the end of useful accounts," breaking out of the scan early [1](#0-0) . This is the same "detect a sentinel/zero condition and stop, without verifying it against the storage's actual declared length" pattern as the `BigNumber` bug: `innerModExp`/`privateRightShift` scanned for a "stopping condition" (leading non-zero word) without bounding the scan to the buffer's real length, so an all-zero result silently produced wrong output (walking past the buffer) instead of being handled based on the buffer's true length.

### Finding Description
The scan loop reads `(meta, account_meta)` pairs sequentially from the append-vec file, and uses the heuristic `account_meta.lamports == 0 && meta.pubkey == Pubkey::default()` to detect "we passed the last useful account" [2](#0-1) . This mirrors `AppendVec::write_accounts`'s zero-padding of any leftover space after the last account, i.e. the design assumes that only trailing zero-padding will match this sentinel.

However, nothing about the on-disk format or this function actually proves that a *legitimate stored account* can never have both `lamports == 0` and `pubkey == Pubkey::default()`. Zero-lamport accounts are explicitly a normal, common state in AccountsDB (they are stored with `lamports = 0` while being flushed/shrunk/rewritten, e.g. `do_flush_slot_cache` and `shrink_storage` both handle zero-lamport accounts as first-class, non-error data [3](#0-2) [4](#0-3) ). If any code path (e.g. `Pubkey::default()` colliding with a real system-program-owned zero-lamport account, or corruption/replay artifacts leaving a stray zeroed record in the middle of a valid append-vec) ever produces a mid-file record matching this sentinel, the scan stops there — exactly like the `BigNumber` loop stopping at the first non-zero word it finds regardless of whether that reflects the true logical length of the result.

Every subsequent account physically present in that storage file after the sentinel becomes invisible to:
- `generate_index_for_slot` (index construction at startup/snapshot load) [5](#0-4) 
- `get_unique_accounts_from_storage`/`get_unique_accounts_from_storage_for_shrink` (shrink and ancient-append-vec packing) [6](#0-5) 
- lattice-hash accumulation via `lt_hash_account`/`mix_in` during index generation [7](#0-6) 

This is the accounts-index/hashing analog of the reported bug class: a loop whose termination condition is a value pattern ("zero") rather than the declared/backing length, silently producing a truncated/incorrect result instead of an error.

### Impact Explanation
If the sentinel condition is ever satisfied mid-file (not just in the true trailing pad), the consequences map directly onto the "Accept" criteria: accounts stored after that point are dropped from the index entirely, which means their lt-hash contribution is never mixed in, producing an accounts-lattice-hash / capitalization divergence between the affected node and honest peers, and a stale/missing account load for every truncated pubkey (the account would appear "not found" instead of its real state). Because `generate_index_for_slot` is exactly the code exercised on snapshot load/startup and by shrink, this can manifest as an honest-node snapshot-vs-replay mismatch or a capitalization-mismatch panic at snapshot load (`bank_from_snapshot_archives` checks `bank.capitalization() != info.calculated_capitalization`) [8](#0-7) .

### Likelihood Explanation
This is speculative rather than confirmed: I was not able to prove, within the tool budget, that a legitimate stored record can actually reach `lamports == 0 && pubkey == Pubkey::default()` in the *middle* of a real append-vec (as opposed to only in the zero-padded tail written by `write_accounts`). The comments in the surrounding tests (`test_scan_useless_accounts`) show this exact sentinel is deliberately used to mark a record as "useless," implying the current test coverage validates the intended trailing-zero case but does not test whether a non-trailing, legitimately-zero-lamport `Pubkey::default()` account elsewhere in the file is handled correctly. Given `Pubkey::default()` (all-zero pubkey) is a possible, if rare, real address, and zero-lamport accounts are routine, I cannot rule this out without further investigation (e.g. checking whether upstream code guarantees `Pubkey::default()` can never be a stored account key, or whether corruption/replay could place a zero-lamport all-default record before genuine trailing padding).

### Recommendation
Bound the scan by the storage's actual on-disk/declared length (already known via `self.len()` and used to size the reader, see `reader.set_file(&self.file, self.len() as FileSize)` [9](#0-8) ) rather than relying solely on the zero-sentinel heuristic to decide when to stop, and treat "sentinel encountered before end-of-declared-length" as a hard truncation/error path rather than silent success, mirroring the recommended fix for `BigNumber`: check against the true buffer/length bound before trusting the value-based stop condition. It should also be verified whether `Pubkead::default()` can ever legitimately be a stored account key, and if so, disambiguate the "end of useful accounts" sentinel using position/length rather than key+lamports content alone.

### Proof of Concept
Not independently reproduced end-to-end within this session's tool budget (no execution/filesystem access). A concrete repro would need to construct or induce an append-vec storage file containing a legitimate, non-final record with `account_meta.lamports == 0` and `meta.pubkey == Pubkey::default()`, followed by additional genuine stored accounts, then call `scan_accounts`/`generate_index_for_slot` on it and observe that the trailing accounts are dropped from the resulting index and lt-hash — analogous to `test_scan_useless_accounts`, which already exercises the sentinel mechanism but only for the intended trailing-zero-pad case [10](#0-9) .

### Citations

**File:** accounts-db/src/append_vec.rs (L794-794)
```rust
        reader.set_file(&self.file, self.len() as FileSize)?;
```

**File:** accounts-db/src/append_vec.rs (L796-812)
```rust
        let mut min_buf_len = STORE_META_OVERHEAD;
        loop {
            let offset = reader.get_file_offset() as usize;
            let bytes = match reader.fill_buf_required(min_buf_len) {
                Ok([]) => break,
                Ok(bytes) => ValidSlice::new(bytes),
                Err(err) if err.kind() == std::io::ErrorKind::UnexpectedEof => break,
                Err(err) => return Err(AppendVecError::Io(err)),
            };

            let (meta, next) = Self::get_type::<StoredMeta>(bytes, 0).unwrap();
            let (account_meta, next) = Self::get_type::<AccountMeta>(bytes, next).unwrap();
            if account_meta.lamports == 0 && meta.pubkey == Pubkey::default() {
                // we passed the last useful account
                break;
            }
            let (_hash, next) = Self::get_type::<ObsoleteAccountHash>(bytes, next).unwrap();
```

**File:** accounts-db/src/append_vec.rs (L1341-1417)
```rust
    /// Test that scanning accounts correctly handles useless accounts.
    #[test]
    fn test_scan_useless_accounts() {
        let num_accounts = 33;
        let num_new_accounts = num_accounts - 2;
        let (av_writer, stored_accounts_info, test_accounts, path) =
            rand_exhaustive_append_vec(num_accounts);
        let av_current_len = av_writer.len();
        av_writer.flush().unwrap();

        // Rewrite the append vec on disk to mark account at num_new_accounts as
        // useless. This will also "hide" any accounts later in the file.
        let stored_meta_offset = stored_accounts_info.offsets[num_new_accounts];
        let account_meta_offset = stored_meta_offset + mem::size_of::<StoredMeta>();
        let new_stored_meta = StoredMeta {
            write_version_obsolete: 0,
            data_len: 0,
            pubkey: Pubkey::default(),
        };
        let new_account_meta = AccountMeta {
            lamports: 0,
            rent_epoch: 0,
            owner: Pubkey::default(),
            executable: false,
        };
        {
            let mut file = OpenOptions::new().write(true).open(&path.path).unwrap();
            file.seek(SeekFrom::Start(stored_meta_offset as u64))
                .unwrap();
            let stored_meta_bytes: &[u8] = unsafe {
                slice::from_raw_parts(
                    ptr::from_ref(&new_stored_meta).cast(),
                    mem::size_of::<StoredMeta>(),
                )
            };
            file.write_all(stored_meta_bytes).unwrap();
            file.seek(SeekFrom::Start(account_meta_offset as u64))
                .unwrap();
            let account_meta_bytes: &[u8] = unsafe {
                slice::from_raw_parts(
                    ptr::from_ref(&new_account_meta).cast(),
                    mem::size_of::<AccountMeta>(),
                )
            };
            file.write_all(account_meta_bytes).unwrap();
            file.flush().unwrap();
        }

        let file_info = FileInfo::new_from_path(&path.path).unwrap();
        let av_reader = AppendVec::new_from_file_info_unchecked(file_info, av_current_len).unwrap();
        let mut reader = new_scan_accounts_reader();
        let mut index = 0;
        av_reader
            .scan_accounts_stored_meta(&mut reader, |stored_account| {
                let (pubkey, account) = &test_accounts[index];
                let recovered = create_account_shared_data(&stored_account);
                assert_eq!(stored_account.pubkey(), pubkey);
                assert_eq!(recovered, *account);
                index += 1;
            })
            .expect("must scan accounts storage");
        assert_eq!(index, num_new_accounts);
        let mut index = 0;
        av_reader
            .scan_stored_accounts_no_data(|stored_account| {
                let (pubkey, account) = &test_accounts[index];
                assert_eq!(stored_account.pubkey(), pubkey);
                assert_eq!(stored_account.lamports(), account.lamports());
                assert_eq!(stored_account.owner(), account.owner());
                assert_eq!(stored_account.data_len(), account.data().len() as u64);
                assert_eq!(stored_account.executable(), account.executable());
                assert_eq!(stored_account.rent_epoch(), account.rent_epoch());
                index += 1;
            })
            .expect("must scan accounts storage");
        assert_eq!(index, num_new_accounts);
    }
```

**File:** accounts-db/src/accounts_db.rs (L2428-2444)
```rust
                let mut do_populate_accounts_for_shrink = |ref_count, slot_list| {
                    if stored_account.is_zero_lamport() && ref_count == 1 {
                        // The lone instance of a zero-lamport account. A load of a zero-lamport
                        // account already reports "not found", so dropping its index entry is safe.
                        zero_lamport_single_ref_pubkeys.push(pubkey);
                        if !can_purge_zero_lamport_single_ref {
                            // Newer than the latest full snapshot: keep the bytes in storage as a
                            // tombstone so an incremental snapshot can still propagate the deletion,
                            // rather than dropping it.
                            tombstones.push(*stored_account);
                        }
                    } else {
                        all_are_zero_lamports &= stored_account.is_zero_lamport();
                        alive_accounts.add(ref_count, stored_account, slot_list);
                        alive += 1;
                    }
                };
```

**File:** accounts-db/src/accounts_db.rs (L2489-2521)
```rust
    pub fn get_unique_accounts_from_storage(
        &self,
        store: &AccountStorageEntry,
    ) -> GetUniqueAccountsResult {
        let written_bytes = store.written_bytes();
        let mut stored_accounts = Vec::with_capacity(store.count());
        store
            .accounts
            .scan_accounts_without_data(|offset, account| {
                // file_id is unused and can be anything. We will always be loading whatever storage is in the slot.
                let file_id = 0;
                stored_accounts.push(AccountFromStorage {
                    index_info: AccountInfo::new(
                        StorageLocation::AppendVec(file_id, offset),
                        account.is_zero_lamport(),
                    ),
                    pubkey: *account.pubkey(),
                    data_len: account.data_len as u64,
                });
            })
            .expect("must scan accounts storage");

        // sort by pubkey bin to keep account index lookups close
        stored_accounts.sort_unstable_by_key(|account| {
            self.accounts_index
                .bin_calculator
                .bin_from_pubkey(account.pubkey())
        });

        GetUniqueAccountsResult {
            stored_accounts,
            written_bytes,
        }
```

**File:** accounts-db/src/accounts_db.rs (L4479-4492)
```rust
                if should_store
                    && account.is_zero_lamport()
                    && !self
                        .accounts_index
                        .get_and_then(key, |entry| (true, entry.is_some()))
                {
                    // A zero-lamport account with no index entry has no older rooted version
                    // in storage to shadow, so it can just be skipped
                    flush_stats.num_zero_lamport_accounts_skipped += 1;
                    if !self.account_indexes.is_empty() {
                        skipped_zero_lamport_pubkeys.push(*key);
                    }
                    should_store = false;
                }
```

**File:** accounts-db/src/accounts_db.rs (L5730-5786)
```rust
        let num_obsolete_accounts_skipped = storage
            .scan_accounts(reader, |offset, account| {
                let data_len = account.data.len();
                stored_size_alive += storage.accounts.calculate_stored_size(data_len);
                let is_account_zero_lamport = account.is_zero_lamport();
                if !is_account_zero_lamport {
                    accounts_data_len += data_len as u64;
                    all_accounts_are_zero_lamports = false;
                } else {
                    // All zero lamport accounts are obsolete or single ref by the end of index
                    // generation. Store the offsets so they can be batch inserted later
                    zero_lamport_offsets.push(offset);
                }
                keyed_account_infos.push((
                    *account.pubkey,
                    AccountInfo::new(
                        StorageLocation::AppendVec(store_id, offset), // will never be cached
                        is_account_zero_lamport,
                    ),
                ));

                if !self.account_indexes.is_empty() {
                    self.accounts_index.update_secondary_indexes(
                        account.pubkey,
                        &account,
                        &self.account_indexes,
                    );
                }

                let account_lt_hash = Self::lt_hash_account(&account, account.pubkey());
                accum.lt_hash.mix_in(&account_lt_hash.0);

                // SAFETY: The bank capitalization field is a u64, so the lamport sum of
                // all accounts modified in a single slot must fit into a u64.
                capitalization = capitalization
                    .checked_add(account.lamports())
                    .expect("capitalization cannot overflow");

                if let Some(geyser_notifier) = geyser_notifier {
                    debug_assert!(geyser_notifier.snapshot_notifications_enabled());
                    let account_for_geyser = AccountForGeyser {
                        pubkey: account.pubkey(),
                        lamports: account.lamports(),
                        owner: account.owner(),
                        executable: account.executable(),
                        rent_epoch: account.rent_epoch(),
                        data: account.data(),
                    };
                    geyser_notifier.notify_account_restore_from_snapshot(
                        slot,
                        write_version_for_geyser,
                        &account_for_geyser,
                    );
                    write_version_for_geyser += 1;
                }
            })
            .expect("must scan accounts storage");
```

**File:** runtime/src/snapshot_bank_utils.rs (L224-233)
```rust
    if bank.capitalization() != info.calculated_capitalization {
        // When limit_load_slot_count is set, ignore capitalization mismatches.
        // Because skipped slots may have changed the calculated capitalization,
        // causing a mismatch with the bank's capitalization.
        if limit_load_slot_count_from_snapshot.is_none() {
            return Err(SnapshotError::MismatchedCapitalization(
                bank.capitalization(),
                info.calculated_capitalization,
            ));
        }
```
