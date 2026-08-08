### Title
Single storage read error during index generation panics and aborts the entire startup index build - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::generate_index()` builds the accounts index at validator startup by scanning every on-disk `AccountStorageEntry` in parallel worker threads. Each worker calls `generate_index_for_slot()`, which invokes `storage.scan_accounts(...).expect("must scan accounts storage")`. If the scan for a single storage/slot returns `Err(AccountsFileError)` (e.g. an I/O read error or a truncated append-vec left behind by an unclean shutdown or a legitimate filesystem hiccup), the `.expect()` panics inside that worker thread. Because `generate_index()` runs all workers inside a `thread::scope` and then does a hard `panic!("index generation failed")` whenever any worker's `.join()` returns `Err`, one bad/unreadable storage file aborts index generation for *every* other (perfectly valid) storage as well, crashing the entire startup path instead of isolating/skipping the one problematic slot.

### Finding Description
The scan chain is:
- `AppendVec::scan_accounts_stored_meta` / `scan_stored_accounts_no_data` return `Result<(), AppendVecError>`, converted to `AccountsFileError` in `accounts-db/src/accounts_file.rs`. [1](#0-0) 
- `AccountStorageEntry::scan_accounts` propagates this `Result` unchanged. [2](#0-1) 
- `AccountsDb::generate_index_for_slot` (called once per storage, per worker thread) unwraps that result with `.expect("must scan accounts storage")`, turning any I/O error into a thread panic: [3](#0-2) 
- `AccountsDb::generate_index` spawns `num_cpus::get()` worker threads inside a `thread::scope`, each pulling storages from a shared work queue and calling `generate_index_for_slot` for every storage it dequeues. When any single thread panics (from the `.expect()` above), `thread_handle.join()` returns `Err`, and the outer loop does a hard `panic!("index generation failed")`: [4](#0-3) 

There is no per-storage try/catch-equivalent (no `catch_unwind`, no per-slot skip-and-log, no `Result` bubbling to the caller as a recoverable error). A single storage's transient read failure is architecturally indistinguishable from a fatal/corruption condition, and the atomicity of the `thread::scope` + `panic!` propagation means the failure of one slot's scan destroys the work already computed by every other worker thread for the run, exactly mirroring the report's "single bad report halts whole batch" pattern: no isolation between independent, per-entry (per-storage) units of work in a larger batch (full index/snapshot reconstruction).

This is reachable during ordinary node startup (`generate_index` is called from `reconstruct_accountsdb_from_fields`, used when loading from a snapshot) whenever any one of potentially tens of thousands of storage files becomes momentarily unreadable — e.g. a legitimate `EIO`/`ENOSPC` from the underlying disk, or a storage file truncated by a hard crash right after being created but before its append-vec metadata was fully flushed. None of these require a maliciously crafted snapshot. [5](#0-4) 

### Impact Explanation
This does not corrupt state or lose funds, but it turns a localized, single-slot storage read problem into a complete validator startup failure: the node cannot come up at all until the operator manually diagnoses and removes/repairs the single offending append-vec file. On a large validator with many thousands of storages, an ordinary transient I/O blip on one file (rather than an actual corruption requiring a resync) can cause a full node-restart outage with only a bare panic message and no per-storage diagnostic, which matches the report's "delayed settlement" / "total silence about which entry caused the failure" impact class, translated to validator availability instead of a DeFi protocol tick.

### Likelihood Explanation
Likely low-to-moderate frequency in the aggregate fleet: it requires an I/O error or truncated append-vec on exactly one of many storage files at the moment `generate_index` scans it, which can plausibly occur after unclean shutdowns, disk pressure, or transient storage-layer errors — none of which are attacker-controlled or require a crafted snapshot. Because `generate_index` fans out across `num_cpus::get()` threads processing potentially tens of thousands of storages, the probability of hitting at least one bad read during a full-node reindex/restart is non-negligible over the fleet's lifetime, even though it is rare per individual restart.

### Recommendation
Make storage scanning atomic per-slot instead of atomic across the whole index-generation batch:
1. Change `generate_index_for_slot` (and its caller loop) to return a `Result` instead of calling `.expect()` on `scan_accounts`, propagating the specific slot/storage id.
2. In the worker loop inside `generate_index`, catch the per-storage error, log it with the offending `slot`/`store_id`, and either skip that storage (recording it for a later repair/resync pass) or mark the index generation as "partial" with a clear, actionable error rather than an opaque `panic!("index generation failed")`.
3. Reserve the current panic-and-abort behavior for cases that truly indicate corruption/invariant violations (e.g. duplicate storages per slot), not for ordinary I/O errors on an otherwise valid, out-of-band storage read.

### Proof of Concept
1. Populate an accounts directory with N valid rooted-slot storages (append-vec files) as generated by normal validator operation.
2. Corrupt or truncate exactly one append-vec file on disk (e.g., truncate it mid-record, simulating a crash right after `AccountStorageEntry` creation but before final flush, or make it return `EIO` via a faulty block device/mount) — no snapshot archive tampering required, just a damaged local file as could occur after an unclean shutdown.
3. Start `AccountsDb::generate_index()` (e.g. via `reconstruct_accountsdb_from_fields` at snapshot load / validator startup) over this account path set.
4. Observe: the worker thread scanning the corrupted storage panics at the `.expect("must scan accounts storage")` in `generate_index_for_slot` (accounts_db.rs:5786), causing `thread_handle.join()` to return `Err`, which triggers `panic!("index generation failed")` in `generate_index` (accounts_db.rs:5917-5919) — even though the other N-1 storages were fully valid and already successfully scanned by other worker threads.

### Citations

**File:** accounts-db/src/accounts_file.rs (L34-42)
```rust
/// An enum for AccountsFile related errors.
#[derive(Error, Debug)]
pub enum AccountsFileError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("AppendVecError: {0}")]
    AppendVecError(#[from] AppendVecError),
}
```

**File:** accounts-db/src/account_storage_entry.rs (L304-319)
```rust
    pub(crate) fn scan_accounts<'a>(
        &'a self,
        reader: &mut impl RequiredLenBufFileRead<'a>,
        mut callback: impl for<'local> FnMut(Offset, StoredAccountInfo<'local>),
    ) -> Result<u64, AccountsFileError> {
        let excluded_offsets = self.excluded_offsets();
        let mut num_excluded = 0;
        self.accounts.scan_accounts(reader, |offset, account| {
            if excluded_offsets.contains(&offset) {
                num_excluded += 1;
                return;
            }
            callback(offset, account);
        })?;
        Ok(num_excluded)
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

**File:** accounts-db/src/accounts_db.rs (L5866-5920)
```rust
        thread::scope(|s| {
            let thread_handles = (0..num_threads)
                .map(|i| {
                    thread::Builder::new()
                        .name(format!("solGenIndex{i:02}"))
                        .spawn_scoped(s, || {
                            let mut thread_accum = IndexGenerationAccumulator::with_slots_capacity(
                                num_storages.div_ceil(num_threads),
                            );
                            let mut reader = append_vec::new_scan_accounts_reader();
                            for next_item in storages_orderer.iter() {
                                let storage = next_item.storage;
                                self.generate_index_for_slot(
                                    &mut reader,
                                    &mut thread_accum,
                                    next_item.original_index,
                                    storage,
                                );
                                num_processed.fetch_add(1, Ordering::Relaxed);
                            }
                            thread_accum
                        })
                })
                .collect::<Result<Vec<_>, _>>()
                .expect("spawn threads");
            let logger_thread_handle = thread::Builder::new()
                .name("solGenIndexLog".to_string())
                .spawn_scoped(s, || {
                    let mut last_update = Instant::now();
                    loop {
                        if exit_logger.load(Ordering::Relaxed) {
                            break;
                        }
                        let num_processed = num_processed.load(Ordering::Relaxed);
                        if num_processed == num_storages as u64 {
                            info!("generating index: processed all slots");
                            break;
                        }
                        let now = Instant::now();
                        if now - last_update > Duration::from_secs(2) {
                            info!(
                                "generating index: processed {num_processed}/{num_storages} \
                                 slots..."
                            );
                            last_update = now;
                        }
                        thread::sleep(Duration::from_millis(500))
                    }
                })
                .expect("spawn thread");
            for thread_handle in thread_handles {
                let Ok(thread_accum) = thread_handle.join() else {
                    exit_logger.store(true, Ordering::Relaxed);
                    panic!("index generation failed");
                };
```

**File:** runtime/src/serde_snapshot.rs (L1198-1205)
```rust
    info!("Building accounts index...");
    let start = Instant::now();
    let IndexGenerationInfo {
        accounts_data_len,
        calculated_accounts_lt_hash,
        calculated_capitalization,
    } = accounts_db.generate_index(limit_load_slot_count_from_snapshot, verify_index);
    info!("Building accounts index... Done in {:?}", start.elapsed());
```
