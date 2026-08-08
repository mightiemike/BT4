## Title
Unref'd duplicate-pubkey batches to `store_accounts()`/`store_accounts_without_stakes_cache()` silently diverge the accounts lt hash and capitalization from the actually-stored balance in release builds - (File: `runtime/src/bank/accounts_lt_hash.rs`)

### Summary
The Note-token bug class ("array of updates summed for a total, but a later overwrite silently drops earlier contributions to the same key") has a direct analog in the accounts write path used by `Bank::store_accounts()` and `Bank::store_accounts_without_stakes_cache()`. These functions require callers to guarantee no duplicate pubkeys in the batch they pass, but that invariant is enforced only when `cfg!(debug_assertions)` is true — i.e., it is compiled out entirely in release builds, which is what production validators run.

### Finding Description
`AccountsDb::write_accounts_to_cache()` correctly implements "last write wins" semantics for duplicate pubkeys within one storage batch: it iterates the batch in reverse, keeps only the newest entry per pubkey, and explicitly counts/skips the rest (`num_duplicate_accounts_skipped`). [1](#0-0) 

However, before that store happens, `Bank::store_accounts_without_stakes_cache()` calls `enqueue_off_chain_accounts_lt_hash_updates()` to update the accounts lt hash and (indirectly via `update_bank_hash_stats`) capitalization/bank-hash bookkeeping for the batch. [2](#0-1) 

This function's doc comment states it "does not deduplicate accounts, requiring the caller to ensure there are no duplicates," and it performs a duplicate check that `panic!`s — but that check is gated entirely behind `cfg!(debug_assertions)`: [3](#0-2) 

In a release build this check does not run at all, so if a batch passed to `store_accounts()`/`store_accounts_without_stakes_cache()` contains duplicate pubkeys, `enqueue_off_chain_accounts_lt_hash_updates()` proceeds to process every entry (not just the last), loading `prev_account` fresh from storage for each occurrence and enqueueing a mix-out(prev)/mix-in(curr) lt-hash update per entry: [4](#0-3) 

Because `prev_account` is loaded from storage/ancestors before any of the batch's writes have been applied, every duplicate entry mixes out the *same* stale previous state and mixes in its own version — so the lt hash accumulates contributions from all versions of the duplicated pubkey. Meanwhile, the actual storage/cache write path (`write_accounts_to_cache`) only persists the *last* version of that pubkey. This is exactly the Note-token pattern: a per-entry accumulator (the lt hash / bank hash stats) sums contributions from every array element, while the actual balance store silently overwrites to just the final value for a duplicated key.

The existing test `test_enqueue_off_chain_accounts_lt_hash_updates_catches_duplicates` demonstrates the exact scenario (multiple versions of `pubkey2`/`pubkey3` in one `store_accounts()` call) and confirms it currently only causes a `panic!` in debug builds: [5](#0-4) 

### Impact Explanation
If any off-chain caller of `Bank::store_accounts()` / `store_accounts_without_stakes_cache()` (e.g., partitioned epoch rewards distribution, builtin migrations, or any future off-chain batch-store call site) ever constructs a batch containing the same pubkey twice — whether by a latent logic bug or a future code change — the resulting accounts lt hash and the derived bank hash would silently diverge from the value that would be computed by rehashing the actually-stored accounts (e.g., during `generate_index()` at startup or accounts-hash verification). This is a "silent balance/hash divergence" class of bug: the stored account balance is correct (last write wins), but the lt hash/bank hash bookkeeping is wrong, which can cause an honest-node hash mismatch between live-computed state and a freshly rebuilt/verified state (snapshot-vs-replay divergence), or a spurious capitalization/hash panic if paired with the `debug_assertions` guard removed. This directly maps to "hash/capitalization divergence" and "honest-node snapshot-vs-replay mismatch" in the validation criteria.

### Likelihood Explanation
The protection today relies entirely on a debug-only assertion with no equivalent runtime safeguard in production (release) builds, and the invariant is documented only in code comments rather than enforced by the type system or dedup logic (unlike the on-chain path `enqueue_on_chain_accounts_lt_hash_updates`, which explicitly deduplicates). This makes the bug latent rather than actively exploited by any currently-known call site, but it is a structural gap: any new or modified off-chain batch-store call site that inadvertently introduces a duplicate pubkey (a very easy mistake, analogous to the duplicated `initialAccounts` entries in the Note token bug) will corrupt the lt hash/capitalization silently in production with no crash to reveal it, while the equivalent bug would be caught immediately in a debug build. This asymmetry (caught in debug, silent in release) is the core of the finding.

### Recommendation
Make `enqueue_off_chain_accounts_lt_hash_updates()` deduplicate pubkeys the same way `enqueue_on_chain_accounts_lt_hash_updates()` already does (keep only the last occurrence per pubkey, mixing hash updates from the true previous stored state to the final version), rather than relying on a debug-only panic to catch caller mistakes. Alternatively, promote the duplicate check to run unconditionally (not gated by `cfg!(debug_assertions)`) so that any violation of the "no duplicates" contract fails loudly in production instead of silently corrupting the lt hash/capitalization bookkeeping.

### Proof of Concept
Compile agave in release mode (`cargo build --release`, i.e., without `debug_assertions`) and call:
```rust
let pubkey = Pubkey::new_unique();
let accounts = [
    (&pubkey, &AccountSharedData::new(100, 0, &Pubkey::default())),
    (&pubkey, &AccountSharedData::new(200, 0, &Pubkey::default())),
];
bank.store_accounts((bank.slot(), accounts.as_slice()), None);
```
In a release build this does not panic (the `cfg!(debug_assertions)` check in `enqueue_off_chain_accounts_lt_hash_updates`, at `runtime/src/bank/accounts_lt_hash.rs:100-127`, is compiled out). The stored account ends up with 200 lamports (last write wins, per `write_accounts_to_cache` at `accounts-db/src/accounts_db.rs:5408-5436`), but the enqueued lt-hash updates mix in both the 100- and 200-lamport versions against the same stale previous state, producing an accounts lt hash that does not match what `AccountsDb::generate_index()`/hash-verification would compute by rehashing the final on-disk state — reproducing, in the accounts-hashing subsystem, the same "sum over duplicated array entries vs. overwritten final value" divergence described in the Note-token report.

### Citations

**File:** accounts-db/src/accounts_db.rs (L5408-5436)
```rust
    // Stores accounts in the write cache. If an account is zero-lamport and not present in the
    // cache or index, there is no need to store it in the write cache as it will not affect the
    // accounts hash. The function returns a BitVec indicating whether each account was stored in
    // the cache. Ordering of accounts is important as duplicate pubkeys are possible. The last
    // account in accounts_and_meta_to_store for each pubkey is stored in the write cache.
    fn write_accounts_to_cache<'a, 'b>(
        &self,
        slot: Slot,
        accounts_and_meta_to_store: &impl StorableAccounts<'b>,
        ancestors: &Ancestors,
    ) -> (BitVec, WriteAccountsToCacheStats) {
        let len = accounts_and_meta_to_store.len();
        let mut pubkey_set = HashSet::with_capacity_and_hasher(len, PubkeyHasherBuilder::default());
        let mut stats = WriteAccountsToCacheStats {
            num_initial_accounts_to_store: len as u64,
            ..Default::default()
        };
        let mut store_account = BitVec::new_fill(false, len as u64);

        (0..len).rev().for_each(|index| {
            accounts_and_meta_to_store.account_default_if_zero_lamport(index, |account| {
                let pubkey = account.pubkey();
                let is_duplicate_account = !pubkey_set.insert(*pubkey);
                if is_duplicate_account {
                    // If the same account is written multiple times in the same batch,
                    // only store the latest version
                    stats.num_duplicate_accounts_skipped += 1;
                    return;
                }
```

**File:** runtime/src/bank.rs (L4791-4810)
```rust
    // Store `accounts`, without updating the stakes cache.
    //
    // - Callers must ensure there are no duplicates in `accounts`.
    // - `thread_pool_for_loading_accounts` is used for accounts lt hashing,
    //   to load the previous version of accounts in parallel.
    fn store_accounts_without_stakes_cache<'a>(
        &self,
        accounts: impl StorableAccounts<'a>,
        thread_pool_for_loading_accounts: Option<&ThreadPool>,
    ) {
        assert!(!self.freeze_started());
        self.update_bank_hash_stats(&accounts);
        self.enqueue_off_chain_accounts_lt_hash_updates(
            &accounts,
            thread_pool_for_loading_accounts,
        );
        self.rc
            .accounts
            .store_accounts_par(accounts, self.bank_id(), None, &self.ancestors);
    }
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L85-127)
```rust
    /// Enqueues the accounts lt hash updates for `accounts` to the accounts hasher thread pool.
    ///
    /// This fn is meant to be called by off-chain events, meaning we know/control `accounts`.
    /// Contrasting with `enqueue_on_chain_accounts_lt_hash_updates()`, this fn:
    /// - Does not deduplicate accounts, requiring the caller to ensure there are no duplicates.
    /// - Does not assume loading the previous version of accounts is fast,
    ///   e.g. when storing stake accounts as part of partitioned epoch rewards.
    ///
    /// If Some, `thread_pool_for_hashing_accounts` will be used
    /// to load the previous version of accounts in parallel.
    pub fn enqueue_off_chain_accounts_lt_hash_updates<'a>(
        &self,
        accounts: &impl StorableAccounts<'a>,
        thread_pool_for_loading_accounts: Option<&ThreadPool>,
    ) {
        if cfg!(debug_assertions) {
            // if debug assertions are on, we will check for duplicates
            use ahash::HashSetExt as _;
            let mut seen_accounts = ahash::HashSet::with_capacity(accounts.len());
            let mut duplicate_pubkeys = ahash::HashSet::with_capacity(0); // assume no duplicates
            for index in 0..accounts.len() {
                let pubkey = accounts.pubkey(index);
                if !seen_accounts.insert(pubkey) {
                    // we've already seen this account, so add it to the duplicates list
                    duplicate_pubkeys.insert(pubkey);
                }
            }
            if !duplicate_pubkeys.is_empty() {
                let mut duplicate_accounts = ahash::HashMap::<_, Vec<_>>::default();
                for duplicate_pubkey in duplicate_pubkeys {
                    for index in 0..accounts.len() {
                        let pubkey = accounts.pubkey(index);
                        if pubkey == duplicate_pubkey {
                            duplicate_accounts
                                .entry(pubkey)
                                .or_default()
                                .push(accounts.account(index, |account| account.take_account()));
                        }
                    }
                }
                panic!("duplicate accounts were enqueued for hashing: {duplicate_accounts:?}");
            }
        }
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L129-170)
```rust
        let async_progress = &self.accounts_lt_hash_async_progress;
        let thread_pool_for_hashing_accounts = accounts_hasher_thread_pool();

        // A closure that does the loading and enqueueing, so code is shared
        // whether using the thread_pool_for_loading_accounts or not.
        let load_then_enqueue = |index| {
            let address = accounts.pubkey(index);
            let prev_account = self
                .rc
                .accounts
                .load_with_fixed_root_do_not_populate_read_cache(&self.ancestors, address)
                .map(|(account, _slot)| account);
            let curr_account = accounts.account(index, |account| {
                (account.lamports() != 0).then(|| account.take_account())
            });
            if prev_account.is_none() && curr_account.is_none() {
                // the account was ephemeral; skip it
            } else {
                // the account was modified; enqueue this update
                async_progress.spawn(
                    thread_pool_for_hashing_accounts,
                    AccountsLtHashUpdate {
                        address: *address,
                        prev_account,
                        curr_account,
                    },
                );
            }
        };

        if let Some(thread_pool_for_loading_accounts) = thread_pool_for_loading_accounts {
            // The previous version of accounts must be loaded before subsequent account
            // modifications occur, so ThreadPool::spawn() canot be used here.
            thread_pool_for_loading_accounts.install(|| {
                (0..accounts.len())
                    .into_par_iter()
                    .for_each(load_then_enqueue);
            });
        } else {
            (0..accounts.len()).for_each(load_then_enqueue);
        }
    }
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L969-996)
```rust
    /// Ensure enqueue_off_chain_accounts_lt_hash_updates() catches duplicates in debug mode.
    #[should_panic(expected = "duplicate accounts were enqueued for hashing")]
    #[test_case(Features::None; "no features")]
    #[test_case(Features::All; "all features")]
    fn test_enqueue_off_chain_accounts_lt_hash_updates_catches_duplicates(features: Features) {
        use rand::seq::SliceRandom as _;
        let (genesis_config, _) = genesis_config_with(features);
        let bank = Bank::new_for_tests(&genesis_config);

        let pubkey1 = pubkey::new_rand();
        let pubkey2 = pubkey::new_rand();
        let pubkey3 = pubkey::new_rand();

        let mut accounts = [
            // one version of pubkey1
            (&pubkey1, &AccountSharedData::new(11, 0, &Pubkey::default())),
            // two versions of pubkey2
            (&pubkey2, &AccountSharedData::new(21, 0, &Pubkey::default())),
            (&pubkey2, &AccountSharedData::new(22, 0, &Pubkey::default())),
            // three versions of pubkey3
            (&pubkey3, &AccountSharedData::new(31, 0, &Pubkey::default())),
            (&pubkey3, &AccountSharedData::new(32, 0, &Pubkey::default())),
            (&pubkey3, &AccountSharedData::new(33, 0, &Pubkey::default())),
        ];
        accounts.shuffle(&mut rand::rng());

        bank.store_accounts((bank.slot(), accounts.as_slice()), None);
    }
```
