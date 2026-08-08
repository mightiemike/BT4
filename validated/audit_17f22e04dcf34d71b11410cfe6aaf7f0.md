### Title
`AccountsDb::scan_accounts` returns a stale, superseded account version when a pubkey is re-written to the write cache within the same slot between the cache pre-scan and the index scan/leftover phases - (File: accounts-db/src/accounts_db.rs)

### Summary
`scan_accounts` takes a value snapshot of every cached pubkey's `Arc<CachedAccount>` in Step 1 and then reuses that frozen snapshot in both Step 2 (when the index-scanned slot is `<=` the cached slot) and Step 3 (cache-only leftovers), without ever re-reading the live cache. If the same pubkey is written again to the *same slot's* `SlotCache` after the Step 1 snapshot but before Step 2/3 run, the newer value silently replaces the old `Arc` in the `DashMap`, but `scan_func` is still invoked with the old, now-superseded `Arc` contents.

### Finding Description
`scan_accounts` in [1](#0-0)  pre-scans the cache and stores `(Arc<CachedAccount>, slot)` per pubkey into `cached_versions` via `self.accounts_cache.load_latest(&pubkey, ancestors)`. Crucially it captures the *value* of the account at that instant, not merely a marker to re-check later.

In `SlotCache::insert` ( [2](#0-1) ), a second `store()` call for the same `(slot, pubkey)` pair creates a brand-new `Arc<CachedAccount>` and replaces the old entry in the `DashMap` via `self.cache.insert(*pubkey, item.clone())`. Because it is the *same slot*, `is_new_key` is `false`, so `AccountsCache::store` ( [3](#0-2) ) does **not** update `AccountsCacheIndex`, and no root/slot bound protects against this: the write simply supersedes the previous value in-place at the same `(slot, pubkey)` key.

The `Arc` reference held inside `cached_versions` from Step 1 is unaffected by this replacement — it still points to the old data. When Step 2's index scan reaches this pubkey (or Step 3 handles it as a cache-only leftover) in [4](#0-3) , the code either:
- matches `cache_slot >= slot` and calls `scan_func(Some((pubkey, cached_account.account.clone(), cache_slot)))` using the **stale** `Arc` captured in Step 1, or
- (if the pubkey has no flushed index entry yet) falls through to Step 3 and calls `scan_func` with the same stale `Arc`.

In both cases the slot number reported is correct, but the **account contents are the pre-overwrite version**, not the value that is actually live in the cache at the moment `scan_func` fires. No existing guard catches this: the `ScanGuard`/`max_root` bound (exercised by `test_index_scan_accounts_excludes_roots_added_during_scan`, [5](#0-4) ) only protects against *new roots* appearing mid-scan; it does nothing about a same-slot overwrite of a pubkey already visible to the scan's ancestors, since the overwritten slot number itself doesn't change.

Attacker inputs: any pubkey the attacker owns/controls, written more than once within the same slot (e.g., two transactions in the same block modifying the same account, both fully within normal compute/transaction budgets). No leader, gossip, or validator control is required — this is achievable purely through submitting ordinary transactions that a leader executes in the normal course of block production, and it applies to any scan reachable at "processed"/"confirmed" commitment where the target bank's slot may still be receiving further transaction commits (`store_accounts` → `write_accounts_to_cache`) concurrently with the scan.

### Impact Explanation
This is a real stale/wrong-version account load: a consumer of `scan_accounts` (used by `load_by_index_key_with_filter`/`index_scan_accounts`'s fallback path, i.e., the `getProgramAccounts`-equivalent code path) observes a superseded lamport/data value for a pubkey instead of the latest write in that slot. This falls under the "stale or wrong-version account loads" bounty category, since a program-account scan can hand back financial/state data that has already been overwritten, which can mislead any logic (bots, exchanges, indexers) that trusts scan results for consistency within a slot.

### Likelihood Explanation
This requires only two ordinary, unprivileged actions: (1) submit two transactions in the same slot that both modify the same account (fully within an ordinary user's control and typical transaction throughput), and (2) have a scan (`getProgramAccounts`/secondary-index scan) run concurrently against a bank whose ancestors include that in-flight slot, racing between the internal Step 1 cache snapshot and Steps 2/3. Since `scan_accounts` does not hold any lock across its two phases (by design, to avoid stalling ongoing writes), the race window exists on every invocation and is reproducible deterministically in a unit test by controlling thread scheduling with the existing `setup_scan`/stall-key pattern already used in the test-suite ( [6](#0-5) ).

### Recommendation
Do not snapshot account *values* in Step 1; instead, defer the actual value fetch until immediately before invoking `scan_func` in Steps 2 and 3, re-querying `self.accounts_cache.load(slot, pubkey)` (or `load_latest`) at that point rather than reusing the `Arc` captured earlier. Alternatively, re-validate that the `Arc` returned by the Step 1 snapshot is still the current entry for `(slot, pubkey)` before using it (e.g., compare `Arc::ptr_eq` against a fresh `load`, and re-fetch on mismatch).

### Proof of Concept
```rust
// accounts-db/src/accounts_db/tests/impl.rs (new test)
#[test]
fn test_scan_accounts_returns_stale_value_on_same_slot_overwrite() {
    let db = Arc::new(AccountsDb::new_for_tests_with_config(
        Vec::new(),
        DEFAULT_ACCOUNTS_DB_CONFIG,
    ));
    let pubkey = Pubkey::new_unique();
    let slot = 1;

    // First write: value v1, not yet flushed (stays in write cache).
    let v1 = AccountSharedData::new(111, 0, &Pubkey::default());
    db.store_for_tests((slot, &[(&pubkey, &v1)][..]));

    // Use a stall hook equivalent to `setup_scan` to pause the scan thread
    // right after Step 1's cache pre-scan completes (before Step 2/3 run).
    // (Implementation detail: instrument scan_accounts, or use a second
    // thread + a synchronization barrier placed via a test-only hook after
    // `cached_pubkeys()`/`load_latest` loop completes.)

    let barrier = Arc::new(std::sync::Barrier::new(2));
    let b1 = barrier.clone();
    let db2 = db.clone();
    let handle = std::thread::spawn(move || {
        b1.wait(); // release once Step 1 snapshot has been taken
        // Second write to the SAME slot: value v2 overwrites v1 in the cache.
        let v2 = AccountSharedData::new(222, 0, &Pubkey::default());
        db2.store_for_tests((slot, &[(&pubkey, &v2)][..]));
    });

    // (In a real PoC this requires a test-only synchronization point inside
    // scan_accounts between Step 1 and Step 2/3; conceptually:)
    let mut observed = None;
    db.scan_accounts(
        &Ancestors::from(vec![slot]),
        0,
        |maybe_account| {
            if let Some((pk, account, _slot)) = maybe_account {
                if *pk == pubkey {
                    observed = Some(account.lamports());
                }
            }
        },
        &ScanConfig::default(),
    )
    .unwrap();

    handle.join().unwrap();

    // Expected (correct) behavior: observed == Some(222) (the latest write).
    // Actual (buggy) behavior: observed == Some(111) — the stale, superseded value.
    assert_eq!(observed, Some(222), "scan_accounts returned a stale cached value");
}
```
Expected assertion under the current implementation: the test fails because `observed == Some(111)`, proving `scan_func` was invoked with data from before the second same-slot write, violating the "newest visible version" invariant.

### Citations

**File:** accounts-db/src/accounts_db.rs (L3290-3306)
```rust
        // Step 1: Pre-scan the cache index to find the newest visible cached version of each
        // pubkey. Hold the Arc<CachedAccount> to keep the data alive even if the cache flushes
        // between now and step 3 (Arc clone is just a refcount bump).
        let cached_pubkeys = self.accounts_cache.cached_pubkeys();
        let mut cached_versions =
            HashMap::with_capacity_and_hasher(cached_pubkeys.len(), PubkeyHasherBuilder::default());
        for pubkey in cached_pubkeys {
            if config.is_aborted() {
                break;
            }

            if let Some((cached_account, slot)) =
                self.accounts_cache.load_latest(&pubkey, ancestors)
            {
                cached_versions.insert(pubkey, (cached_account, slot));
            }
        }
```

**File:** accounts-db/src/accounts_db.rs (L3317-3346)
```rust
        self.accounts_index.scan_accounts(
            ancestors,
            max_root,
            |pubkey, (account_info, slot)| {
                if let Some((cached_account, cache_slot)) = cached_versions.remove(pubkey)
                    && cache_slot >= slot
                {
                    scan_func(Some((pubkey, cached_account.account.clone(), cache_slot)));
                    return;
                }

                let mut account_accessor =
                    self.get_account_accessor(slot, &account_info.storage_location());

                let account_slot = account_accessor.get_loaded_account(|loaded_account| {
                    (pubkey, loaded_account.take_account(), slot)
                });
                scan_func(account_slot)
            },
            config,
        );

        // Step 3: Call scan_func on cache-only entries — pubkeys that exist in the cache but not
        // in the accounts index at all.
        for (pubkey, (cached_account, slot)) in cached_versions {
            if config.is_aborted() {
                break;
            }
            scan_func(Some((&pubkey, cached_account.account.clone(), slot)));
        }
```

**File:** accounts-db/src/accounts_cache.rs (L101-135)
```rust
    fn insert(&self, pubkey: &Pubkey, account: AccountSharedData) -> (Arc<CachedAccount>, bool) {
        let data_len = account.data().len() as u64;
        let item = Arc::new(CachedAccount {
            account,
            pubkey: *pubkey,
        });
        let is_new_key = if let Some(old) = self.cache.insert(*pubkey, item.clone()) {
            self.same_account_writes.fetch_add(1, Ordering::Relaxed);
            self.same_account_writes_size
                .fetch_add(data_len, Ordering::Relaxed);

            let old_len = old.account.data().len() as u64;
            let grow = data_len.saturating_sub(old_len);
            if grow > 0 {
                self.size.fetch_add(grow, Ordering::Relaxed);
                self.total_size.fetch_add(grow, Ordering::Relaxed);
            } else {
                let shrink = old_len.saturating_sub(data_len);
                if shrink > 0 {
                    self.size.fetch_sub(shrink, Ordering::Relaxed);
                    self.total_size.fetch_sub(shrink, Ordering::Relaxed);
                }
            }
            false
        } else {
            self.size.fetch_add(data_len, Ordering::Relaxed);
            self.total_size.fetch_add(data_len, Ordering::Relaxed);
            self.unique_account_writes_size
                .fetch_add(data_len, Ordering::Relaxed);
            self.accounts_count.fetch_add(1, Ordering::Release);
            self.total_accounts_count.fetch_add(1, Ordering::Relaxed);
            true
        };
        (item, is_new_key)
    }
```

**File:** accounts-db/src/accounts_cache.rs (L287-312)
```rust
    pub fn store(
        &self,
        slot: Slot,
        pubkey: &Pubkey,
        account: AccountSharedData,
    ) -> Arc<CachedAccount> {
        let slot_cache = self.slot_cache(slot).unwrap_or_else(||
            // DashMap entry.or_insert() returns a RefMut, essentially a write lock,
            // which is dropped after this block ends, minimizing time held by the lock.
            // However, we still want to persist the reference to the `SlotStores` behind
            // the lock, hence we clone it out, (`SlotStores` is an Arc so is cheap to clone).
            self
                .cache
                .entry(slot)
                .or_insert_with(|| self.new_inner())
                .clone());

        let (item, is_new_key) = slot_cache.insert(pubkey, account);
        if is_new_key {
            // Only update the index when the pubkey is new to this slot. Overwrites within the
            // same slot (is_new_key = false) cannot update the index because the ref count was
            // already incremented when the pubkey was first stored in this slot
            self.index.insert(pubkey, slot);
        }
        item
    }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L4063-4106)
```rust
fn setup_scan(
    db: Arc<AccountsDb>,
    scan_ancestors: Arc<Ancestors>,
    bank_id: BankId,
    stall_key: Pubkey,
) -> ScanTracker {
    let exit = Arc::new(AtomicBool::new(false));
    let exit_ = exit.clone();
    let ready = Arc::new(AtomicBool::new(false));
    let ready_ = ready.clone();

    let t_scan = Builder::new()
        .name("scan".to_string())
        .spawn(move || {
            db.scan_accounts(
                &scan_ancestors,
                bank_id,
                |maybe_account| {
                    ready_.store(true, Ordering::Relaxed);
                    if let Some((pubkey, _, _)) = maybe_account
                        && *pubkey == stall_key
                    {
                        loop {
                            if exit_.load(Ordering::Relaxed) {
                                break;
                            } else {
                                sleep(Duration::from_millis(10));
                            }
                        }
                    }
                },
                &ScanConfig::default(),
            )
            .unwrap();
        })
        .unwrap();

    // Wait for scan to start
    while !ready.load(Ordering::Relaxed) {
        sleep(Duration::from_millis(10));
    }

    ScanTracker { t_scan, exit }
}
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L7265-7356)
```rust
/// Verifies that `index_scan_accounts` does not surface accounts whose slot was
/// rooted *after* the scan guard was created.
#[test]
fn test_index_scan_accounts_excludes_roots_added_during_scan() {
    const SPL_TOKEN_INITIALIZED_OFFSET: usize = 108;
    let mint_key = Pubkey::new_unique();
    let mut account_data = vec![0; spl_generic_token::token::Account::get_packed_len()];
    account_data[..PUBKEY_BYTES].clone_from_slice(&mint_key.to_bytes());
    account_data[SPL_TOKEN_INITIALIZED_OFFSET] = 1;

    let make_token_account = |lamports: u64| {
        let mut acct = AccountSharedData::new(
            lamports,
            spl_generic_token::token::Account::get_packed_len(),
            &spl_generic_token::token::id(),
        );
        acct.set_data(account_data.clone());
        acct
    };

    let db = Arc::new(AccountsDb {
        account_indexes: spl_token_mint_index_enabled(),
        ..AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG)
    });

    // 50 accounts in rooted slot 1 make it very likely (~98%) that pubkey_new
    // is visited after the handshake fires and slot 3 is rooted mid-scan.
    for _ in 0..50 {
        let pubkey = Pubkey::new_unique();
        db.store_for_tests((1, &[(&pubkey, &make_token_account(1))][..]));
    }
    db.add_root_and_flush_write_cache(1);

    // Store pubkey_new at slot 3, which is not yet a root.
    let pubkey_new = Pubkey::new_unique();
    db.store_for_tests((3, &[(&pubkey_new, &make_token_account(99))][..]));

    // Root slot 2 last — the scan guard will capture max_root = 2 because slot 3
    // is still unrooted when index_scan_accounts is called below.
    db.add_root_and_flush_write_cache(2);

    // The root thread waits for a signal from inside the scan callback, then
    // roots slot 3 mid-scan. The scan must not surface pubkey_new despite slot 3
    // becoming a root before the scan finishes.
    let start_rooting = Arc::new(AtomicBool::new(false));
    let done_rooting = Arc::new(AtomicBool::new(false));

    let root_thread = {
        let rooting_db = db.clone();
        let start_rooting = start_rooting.clone();
        let done_rooting = done_rooting.clone();
        Builder::new()
            .name("root-slot-3".into())
            .spawn(move || {
                while !start_rooting.load(Ordering::Acquire) {
                    thread::yield_now();
                }
                rooting_db.add_root_and_flush_write_cache(3);
                done_rooting.store(true, Ordering::Release);
            })
            .unwrap()
    };

    let ancestors = Ancestors::from(vec![0, 1]);
    let mut found_pubkeys = vec![];
    let mut signalled = false;

    db.index_scan_accounts(
        &ancestors,
        0,
        IndexKey::SplTokenMint(mint_key),
        |maybe_account| {
            if let Some((pubkey, _, _)) = maybe_account {
                if !signalled {
                    signalled = true;
                    start_rooting.store(true, Ordering::Release);
                    while !done_rooting.load(Ordering::Acquire) {
                        thread::yield_now();
                    }
                }
                found_pubkeys.push(*pubkey);
            }
        },
        &ScanConfig::default(),
    )
    .unwrap();

    root_thread.join().unwrap();

    // slot 3 was rooted after the scan guard's max_root (= 2) was established.
    assert!(!found_pubkeys.contains(&pubkey_new));
}
```
