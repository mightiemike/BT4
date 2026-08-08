### Title
Silent accounts-lattice-hash corruption from duplicate pubkeys in off-chain stake-reward stores in release builds - ([File: runtime/src/bank/accounts_lt_hash.rs])

### Summary
`Bank::store_accounts()` / `Bank::store_accounts_without_stakes_cache()` document an invariant — "Callers must ensure there are no duplicates in `accounts`" — that is only *enforced* when `debug_assertions` are on. In a release validator build, if this invariant is ever violated, `enqueue_off_chain_accounts_lt_hash_updates()` silently mixes the same account update into the lattice hash multiple times, corrupting the lt hash without any error, mirroring the structural root cause of the referenced ERC20 self-transfer bug: a caching/aliasing assumption ("each key appears once") that is checked only in a debug/test path, not in production.

### Finding Description
`Bank::store_accounts()` is documented at [1](#0-0)  as requiring the caller to guarantee no duplicate pubkeys in the batch, and it forwards to `store_accounts_without_stakes_cache()`, which calls `enqueue_off_chain_accounts_lt_hash_updates()` [2](#0-1) .

`enqueue_off_chain_accounts_lt_hash_updates()` explicitly states it "Does not deduplicate accounts, requiring the caller to ensure there are no duplicates," and only performs a duplicate check — which panics — when `cfg!(debug_assertions)` is true: [3](#0-2) . In a release build this check is compiled out entirely.

If duplicates ever reach this path, for each occurrence of the same pubkey the function loads `prev_account` from the accounts store/cache (unaffected by the not-yet-applied in-batch update) and mixes `prev_account` out and `curr_account` in, exactly as in the per-account processing loop: [4](#0-3)  and [5](#0-4) . Because the same stale `prev_account` snapshot is read multiple times (an aliasing/caching pattern directly analogous to `rebasingStateFrom == rebasingStateTo` in the reported bug) and each duplicate's `curr_account` is independently mixed in, the lattice hash accumulator (`AccountsLtHashAsyncProgress`) ends up mixing out the previous value N times and mixing in N different "current" values for what is really only one final on-disk account state — the accumulated lt hash no longer matches the actual account. This differs from the deduplicating sibling function `enqueue_on_chain_accounts_lt_hash_updates()`, which explicitly dedups by walking accounts in reverse and skipping already-seen pubkeys: [6](#0-5) .

The regression test `test_enqueue_off_chain_accounts_lt_hash_updates_catches_duplicates` only proves the debug-mode panic fires; it does not, and cannot, exercise or validate what actually happens in a release build once the assert is stripped: [7](#0-6) .

### Impact Explanation
If any current or future off-chain caller (e.g. partitioned epoch-reward stake-account stores, sysvar updates, or any other code path using `store_accounts()`/`store_accounts_without_stakes_cache()`) ever passes a batch containing the same pubkey twice, a release-mode validator would silently compute a wrong accounts lattice hash while an honest debug/test build would catch it via panic. This produces a hash/capitalization divergence between differently-built nodes (or between a validator that happened to run in debug mode during testing vs. production release binaries), which can manifest as consensus-relevant bank-hash mismatches. This maps to the "hash/capitalization divergence" and "silent balance change" impact classes allowed by the validation criteria. The severity depends entirely on whether such a duplicate-pubkey batch is currently reachable through any call site; the current stake-reward distribution path (`store_stake_accounts_in_partition`) constructs one `StakeReward` per unique index per partition and appears not to duplicate pubkeys today, so this is a latent invariant-enforcement gap rather than a demonstrated live invocation of duplicates today.

### Likelihood Explanation
Likelihood is limited by the fact that no currently-identified call path is confirmed to pass duplicate pubkeys into `store_accounts_without_stakes_cache()`/`store_accounts()`. The risk is that the safety net (the duplicate check) is *debug-only*, so any future refactor, new caller, or an unaccounted edge case in reward/partition construction that introduces a duplicate pubkey would go completely undetected in production release builds, whereas it would be immediately caught in CI/debug testing — creating a false sense of safety. This is analogous to the original report's core flaw: an implicit "no self-reference / no duplicate key" assumption that is not defensively enforced at the point of use.

### Recommendation
Make the duplicate-pubkey check in `enqueue_off_chain_accounts_lt_hash_updates()` unconditional (not gated by `cfg!(debug_assertions)`), or better, perform deduplication (keeping only the last version, like `enqueue_on_chain_accounts_lt_hash_updates()` already does) rather than relying on a caller-side invariant with no production enforcement. At minimum, add a lightweight, always-on assertion (not a full duplicate scan, if performance is a concern) for known-fixed-size call sites like reward distribution to guarantee this invariant holds in all builds.

### Proof of Concept
No release-mode reproduction is possible from static analysis alone because no current call site was found that passes duplicate pubkeys to `store_accounts_without_stakes_cache()`. The debug-mode-only nature of the safety check can be directly observed and confirmed by inspecting the `cfg!(debug_assertions)` gate at [8](#0-7)  and the corresponding test that only asserts the debug panic behavior at [7](#0-6) ; a concrete confirmed exploit would require identifying (via full-codebase/dynamic testing, which is outside the scope of this static review) a call site that can be made to emit duplicate pubkeys in a single off-chain `store_accounts` batch in a release binary.

### Citations

**File:** runtime/src/bank.rs (L4752-4756)
```rust
    // Store `accounts`.
    //
    // - Callers must ensure there are no duplicates in `accounts`.
    // - `thread_pool_for_loading_accounts` is used for accounts lt hashing,
    //   to load the previous version of accounts in parallel.
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

**File:** runtime/src/bank/accounts_lt_hash.rs (L38-66)
```rust
    pub fn enqueue_on_chain_accounts_lt_hash_updates<'a>(
        &self,
        accounts: &impl StorableAccounts<'a>,
    ) {
        if accounts.is_empty() {
            return;
        }

        let seen_accounts_freelist = seen_accounts_freelist();
        let mut seen_accounts = seen_accounts_freelist.try_pop().unwrap_or_default();
        let async_progress = &self.accounts_lt_hash_async_progress;
        let thread_pool = accounts_hasher_thread_pool();

        // process accounts in reverse because we must only count the latest version of each account
        for index in (0..accounts.len()).rev() {
            let address = accounts.pubkey(index);
            if !seen_accounts.insert(*address) {
                // we've already enqueued a newer update for the same account; skip this one
                continue;
            }
            let prev_account = self
                .rc
                .accounts
                .load_with_fixed_root_do_not_populate_read_cache(&self.ancestors, address)
                .map(|(account, _slot)| account);
            let curr_account = accounts.account(index, |account| {
                (account.lamports() != 0).then(|| account.take_account())
            });
            if prev_account.is_none() && curr_account.is_none() {
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

**File:** runtime/src/bank/accounts_lt_hash.rs (L134-157)
```rust
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
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L304-322)
```rust
    /// Processes `update` and mixes the result into `accum_lt_hash`.
    ///
    /// Note: Since an LtHash is large, `accum_lt_hash` is passed as an in-out parameter.
    /// This it to avoid Rust compiler bug that fails to perform return value optimization.
    fn process(accum_lt_hash: &mut LtHash, update: AccountsLtHashUpdate) {
        let AccountsLtHashUpdate {
            address,
            prev_account,
            curr_account,
        } = update;
        if let Some(prev_account) = prev_account {
            let prev_lt_hash = AccountsDb::lt_hash_account(&prev_account, &address);
            accum_lt_hash.mix_out(&prev_lt_hash.0);
        }
        if let Some(curr_account) = curr_account {
            let curr_lt_hash = AccountsDb::lt_hash_account(&curr_account, &address);
            accum_lt_hash.mix_in(&curr_lt_hash.0);
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
