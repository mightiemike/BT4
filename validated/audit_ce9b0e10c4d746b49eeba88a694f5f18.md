### Title
Unchecked subtraction in `AccountStorageReader::new` can underflow and panic during full/incremental snapshot archive generation - ([File: accounts-db/src/account_storage_reader.rs])

### Summary
`AccountStorageReader::new` computes the number of alive bytes to stream into a snapshot archive using plain (unchecked) integer subtraction: `num_total_bytes - storage.get_obsolete_bytes(snapshot_slot)` and `num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size`. This mirrors the reported `maxDeposit` pattern: a value derived from mutable state (`mintCap`) is subtracted from another value (`totalSupply`) without guarding against the subtrahend exceeding the minuend, causing a revert/panic instead of a graceful fallback.

### Finding Description
`AccountStorageReader::new` is constructed like this: [1](#0-0) 

and later: [2](#0-1) 

Both subtractions assume `get_obsolete_bytes(snapshot_slot)` (plus, separately, `tombstone_offsets.len() * tombstone_stored_size`) can never exceed `num_total_bytes` / the already-reduced `num_alive_bytes`. `num_total_bytes` comes from `storage.accounts.len()` (the physical size of the append-vec on disk), while `get_obsolete_bytes` and the tombstone count are derived from separately tracked, concurrently-updated bookkeeping structures (`obsolete_accounts`, `tombstone_offsets`) on `AccountStorageEntry` [3](#0-2) . If these bookkeeping structures ever become inconsistent with the storage's actual byte count (e.g., due to double-counting an account as both obsolete and a tombstone, a stale/racy update to `obsolete_accounts` from concurrent shrink/clean, or an accounting bug elsewhere in the shrink/clean/obsolete-marking machinery), the subtraction underflows a `usize`. In debug builds this panics immediately; in release builds it silently wraps to a huge `usize`, which would then be used as `num_alive_bytes`/the reader's reported `len()` — corrupting the snapshot archive's size/content is used by `Read::read` (which relies on `num_total_bytes` for bounds, but a wrapped `num_alive_bytes` returned by `len()` is used by callers to size the output file and to verify snapshot integrity).

This is directly analogous to the reported `maxDeposit` bug: an unguarded subtraction of two logically related but independently mutated quantities, where a legitimate state transition (mintCap change / obsolete-accounting drift) can make the subtrahend exceed the minuend.

### Impact Explanation
This code path runs during full and incremental snapshot archive generation (`AccountStorageReader::new` is used from `snapshots/src/archive.rs`), which happens automatically as part of routine node operation (not a special operator action) whenever a snapshot is taken. A panic here would crash/abort the snapshot-generation thread, and depending on how errors are propagated, could stop the `AccountsBackgroundService`'s snapshot handling entirely (similar in effect to the fatal-error handling seen in `accounts_background_service.rs`, where snapshot-handler errors terminate the service) [4](#0-3) . A release-mode silent wraparound would instead produce a corrupted/incorrectly-sized snapshot archive, which is a snapshot-generation correctness issue.

### Likelihood Explanation
Likelihood depends on whether `get_obsolete_bytes`/tombstone accounting can ever legitimately (or via a latent bug) exceed the storage's total byte count for a given `snapshot_slot`. I was not able to fully verify the invariant that guarantees `get_obsolete_bytes(snapshot_slot) + tombstones_size <= num_total_bytes` always holds across all code paths that mutate `obsolete_accounts`/`tombstone_offsets` (e.g., shrink's "carry forward" of tombstones, concurrent obsolete-marking during clean) — this would require deeper review of `accounts-db/src/obsolete_accounts.rs` and all call sites that mark accounts obsolete or as tombstones. Given the existing code elsewhere in this same crate consistently uses `checked_sub`/`saturating_sub`/`.expect("...cannot underflow")` for comparable subtractions (e.g., `accounts_db.rs` lines 6108-6112, `account_storage_reader.rs` lines 156-172 in `Read::read`), the plain `-`/`-=` operators in the constructor stand out as an inconsistency, suggesting this invariant may not be as rigorously enforced/tested here as elsewhere.

### Recommendation
Replace the unchecked subtractions with `checked_sub`/`saturating_sub` and either return an `io::Error` from `AccountStorageReader::new` or `saturating_sub` to 0 with a debug assertion, similar to the pattern already used in `Read::read` in the same file (`saturating_sub`) and in `accounts_db.rs` (`checked_sub(...).expect(...)`). This avoids a hard panic/undefined wraparound during snapshot generation, and instead fails safely or logs the inconsistency for investigation.

### Proof of Concept
Not able to construct a concrete PoC without deeper verification of the obsolete/tombstone accounting invariants across shrink/clean; a reproducer would need to drive `AccountStorageEntry` into a state where `get_obsolete_bytes(snapshot_slot)` (or `get_obsolete_bytes(snapshot_slot) + tombstone_bytes`) exceeds `storage.accounts.len()`, then call `AccountStorageReader::new` with that `snapshot_slot`, e.g. by extending the existing test `test_account_storage_reader_filter_by_slot` [5](#0-4)  to inject inconsistent obsolete/tombstone bookkeeping.

### Citations

**File:** accounts-db/src/account_storage_reader.rs (L99-101)
```rust
    ) -> io::Result<Self> {
        let num_total_bytes = storage.accounts.len();
        let mut num_alive_bytes = num_total_bytes - storage.get_obsolete_bytes(snapshot_slot);
```

**File:** accounts-db/src/account_storage_reader.rs (L115-120)
```rust
        if tombstones_filter == TombstonesFilter::Exclude {
            // Tombstones are zero-lamport accounts, which store no data, so every
            // tombstone record has the fixed stored size of a data-less account.
            let tombstone_stored_size = storage.accounts.calculate_stored_size(0);
            let tombstone_offsets = storage.tombstone_offsets_read_lock();
            num_alive_bytes -= tombstone_offsets.len() * tombstone_stored_size;
```

**File:** accounts-db/src/account_storage_reader.rs (L403-524)
```rust
    #[test]
    fn test_account_storage_reader_filter_by_slot() {
        let (storage, _temp_dirs) =
            create_storage_for_storage_reader(10, AccountsFileProvider::AppendVec);
        let total_accounts = 30;

        let slot = 0;

        // Create a bunch of accounts and add them to the storage
        let accounts: Vec<_> =
            iter::repeat_with(|| AccountSharedData::new(1, 10, &Pubkey::default()))
                .take(total_accounts)
                .collect();

        let accounts_to_append: Vec<_> = accounts
            .into_iter()
            .map(|account| (Pubkey::new_unique(), account))
            .collect();

        let offsets = storage
            .accounts
            .write_accounts(&(slot, &accounts_to_append[..]));

        // Generate a seed from entropy and log the original seed
        let seed: u64 = rand::random();
        info!("Generated seed: {seed}");

        // Use a seedable RNG with the generated seed for reproducibility
        let mut rng = StdRng::seed_from_u64(seed);

        let max_offset = offsets
            .as_ref()
            .and_then(|offsets| offsets.offsets.iter().max().cloned())
            .unwrap();

        let mut obsolete_account_offset = offsets
            .map(|offsets| {
                offsets
                    .offsets
                    .choose_multiple(&mut rng, total_accounts - 1)
                    .cloned()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        // Ensure that the last entry will be marked obsolete at some point
        if !obsolete_account_offset.contains(&max_offset) {
            // Replace a random obsolete account with the max offset
            if let Some(random_index) = obsolete_account_offset.choose_mut(&mut rng) {
                *random_index = max_offset;
            }
        }

        // Mark the obsolete accounts in storage at different slots
        let mut slot_marked_dead = 0;
        obsolete_account_offset.into_iter().for_each(|offset| {
            let mut size = storage.accounts.get_account_data_lens(&[offset]);
            storage
                .obsolete_accounts()
                .write()
                .unwrap()
                .mark_accounts_obsolete(
                    vec![(offset, size.pop().unwrap())].into_iter(),
                    slot_marked_dead,
                );
            slot_marked_dead += 1;
        });

        // Create a temporary directory
        let temp_dir = tempfile::tempdir().unwrap();

        // Now iterate through all the possible snapshot slots and verify correctness
        let files = open_storage_files(iter::once(&storage), false)
            .collect::<io::Result<Vec<_>>>()
            .unwrap();
        let mut file_reader = storage_file_buf_reader(
            ACCOUNT_STORAGE_MAX_BUFFER_SIZE,
            false,
            &IoSetupState::default(),
        )
        .unwrap();
        for snapshot_slot in 0..slot_marked_dead {
            file_reader
                .set_file(files[0].as_ref(), storage.accounts.len() as u64)
                .unwrap();
            let mut reader = AccountStorageReader::new(
                &storage,
                Some(snapshot_slot),
                TombstonesFilter::Include,
                &mut file_reader,
            )
            .unwrap();
            let current_len =
                storage.accounts.len() - storage.get_obsolete_bytes(Some(snapshot_slot));
            assert_eq!(reader.len(), current_len);

            // Create a file to write the reader's output. It will get deleted by AccountsFile::drop() every
            // iteration so it does not need a unique name
            let temp_file_path = temp_dir.path().join("output_file");
            let mut output_file = File::create(&temp_file_path).unwrap();

            let bytes_written = io::copy(&mut reader, &mut output_file).unwrap();
            assert_eq!(bytes_written as usize, reader.len());

            // Close the file
            drop(output_file);

            let (accounts_file, _num_accounts) =
                AccountsFile::new_from_file(temp_file_path, current_len).unwrap();

            // Create a new AccountStorageEntry from the output file
            let new_storage = AccountStorageEntry::new_existing(
                slot,
                0,
                accounts_file,
                ObsoleteAccounts::default(),
            );

            // Verify that the new storage has the same length as the reader
            assert_eq!(new_storage.accounts.len(), reader.len());
        }
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L46-53)
```rust
    zero_lamport_single_ref_offsets: RwLock<IntSet<Offset>>,

    /// offsets to zero-lamport accounts that have been removed from the accounts index entirely
    /// (a tombstone — carried forward to this storage by shrink). The index has no slot_list entry
    /// pointing at them; their bytes are retained only so an incremental snapshot taken after the
    /// latest full snapshot still observes the zero-lamport account and propagates the deletion.
    /// Shrink uses this list to recognize tombstone entries without needing to scan the index.
    tombstone_offsets: RwLock<IntSet<Offset>>,
```

**File:** runtime/src/accounts_background_service.rs (L515-522)
```rust
                                Err(err) => {
                                    error!(
                                        "Stopping AccountsBackgroundService! Fatal error while \
                                         handling snapshot requests: {err}",
                                    );
                                    exit.store(true, Ordering::Relaxed);
                                    break;
                                }
```
