### Title
Unbounded SPL-Token secondary index growth allows single-call disproportionate CPU cost - (File: accounts-db/src/accounts_index/secondary.rs)

### Summary
Agave's SPL-Token secondary index (`spl_token_owner_index` / `spl_token_mint_index`) maps an attacker-controlled key (the token account's unpacked `owner` or `mint` field) to the set of all token-account pubkeys that carry that value. Because the `owner`/`mint` fields inside SPL token account data are just bytes chosen by whoever creates the account — they are not the account's real authority and are not access-controlled — any unprivileged user can mint an unbounded number of token accounts that all encode the same victim pubkey as `owner`. This inflates a single `SecondaryIndex` entry without any cap, and a single RPC call that consults that entry (`getTokenAccountsByOwner`, secondary-index path) must then materialize and scan the entire inflated set in one shot.

### Finding Description
`AccountsIndex::update_spl_token_secondary_indexes` inserts the unpacked `owner_key`/`mint_key` from account data directly into the secondary index with no bound on how many accounts can share a key: [1](#0-0) 

The insertion path (`SecondaryIndex::insert`) pushes into the forward `index: DashMap<Pubkey, SecondaryIndexEntryType>` keyed by the attacker-chosen `owner_key`, backed by `RwLockSecondaryIndexEntry` (a `HashSet<Pubkey>`), with no size limit: [2](#0-1) [3](#0-2) 

Retrieval (`get_index_key_pubkeys`) returns the *entire* set for a key with no pagination: [4](#0-3) 

That set is consumed by `get_filtered_spl_token_accounts_by_owner` → `get_filtered_indexed_accounts` in the RPC layer, which loads and post-filters every returned pubkey in a single call: [5](#0-4) 

Unlike the reported Curves.sol bug — where the array never shrinks even on legitimate removal — Agave's removal path (`remove_by_inner_key_if`, driven by `purge_secondary_indexes_for_dead_keys`/`handle_dead_keys` on clean, cache flush, and purge) *does* correctly clean up dead entries: [6](#0-5) [7](#0-6) 
So the "leak/never-shrinks" root cause does not carry over. What does carry over from the Curves.sol pattern is the un-capped growth of a per-key collection driven entirely by attacker-chosen input (the `owner` field is unauthenticated), and the fact that a legitimate lookup for that key (`getTokenAccountsByOwner`) must iterate the whole, attacker-inflatable collection.

### Impact Explanation
An attacker can create arbitrarily many SPL token accounts (paying only rent) that all encode the same victim `owner` pubkey, inflating that key's `RwLockSecondaryIndexEntry` set to an arbitrarily large size. A single subsequent `getTokenAccountsByOwner` call against the secondary index for that owner then triggers O(N) pubkey lookups/account loads/filtering in that one RPC call, disproportionately consuming CPU/I/O relative to the cost the attacker paid to create the token accounts. This degrades the responsiveness of the RPC node servicing that request and can be used to target any specific pubkey the attacker wants to make an "expensive" `owner` to query.

### Likelihood Explanation
Requires only that the secondary index (`--account-index spl-token-owner` or `spl-token-mint`) be enabled on the target validator/RPC node — this is an opt-in feature but is commonly enabled by RPC providers to support `getTokenAccountsByOwner`/`getTokenAccountsByMint`. Creating token accounts with an arbitrary `owner` byte value requires no special privilege — any user can construct an SPL Token account with `InitializeAccount` and any owner bytes; the field is not signed/verified against real control of the account. The attack is triggered by a single RPC call once the index key is inflated (not by a burst of many calls), consistent with the "no more than one call" scope constraint.

### Recommendation
Bound the number of inner keys returned/scanned per secondary-index key (e.g. cap `SecondaryIndex::get`/`get_index_key_pubkeys` results, or reject/paginate RPC secondary-index scans once an outer key's set exceeds a configurable threshold), and/or rate-limit or reject `getTokenAccountsByOwner`/`ByMint` requests whose underlying index-key set size exceeds a sane bound, returning an error (similar to existing `KeyExcludedFromSecondaryIndex`) rather than performing the full scan.

### Proof of Concept
1. Enable the SPL-Token-owner secondary index on a validator (`--account-index spl-token-owner`).
2. As an unprivileged attacker, repeatedly submit `InitializeAccount`/`InitializeAccount3` instructions for new SPL Token accounts whose packed `owner` field (per `GenericTokenAccount::unpack_account_owner`) is set to a chosen victim pubkey `V`, funding each new account only with the rent-exempt minimum.
3. Each such account store causes `update_spl_token_secondary_indexes` to call `self.spl_token_owner_index.insert(V, pubkey)`, growing the `HashSet<Pubkey>` behind `V`'s `RwLockSecondaryIndexEntry` without bound: [8](#0-7) 
4. Issue a single `getTokenAccountsByOwner` RPC call for owner `V`; this calls `get_filtered_indexed_accounts` with `IndexKey::SplTokenOwner(V)`, which retrieves and iterates the entire inflated pubkey set built in step 3 in one request: [9](#0-8) 
5. Observe disproportionate CPU/latency cost for that single call relative to the attacker's on-chain spend.

### Citations

**File:** accounts-db/src/accounts_index.rs (L376-383)
```rust
    /// Returns the list of pubkeys from the secondary index for the given key.
    pub(crate) fn get_index_key_pubkeys(&self, index_key: &IndexKey) -> Vec<Pubkey> {
        match index_key {
            IndexKey::ProgramId(key) => self.program_id_index.get(key),
            IndexKey::SplTokenMint(key) => self.spl_token_mint_index.get(key),
            IndexKey::SplTokenOwner(key) => self.spl_token_owner_index.get(key),
        }
    }
```

**File:** accounts-db/src/accounts_index.rs (L557-580)
```rust
    fn update_spl_token_secondary_indexes<G: spl_generic_token::token::GenericTokenAccount>(
        &self,
        token_id: &Pubkey,
        pubkey: &Pubkey,
        account_owner: &Pubkey,
        account_data: &[u8],
        account_indexes: &AccountSecondaryIndexes,
    ) {
        if *account_owner == *token_id {
            if account_indexes.contains(&AccountIndex::SplTokenOwner)
                && let Some(owner_key) = G::unpack_account_owner(account_data)
                && account_indexes.include_key(owner_key)
            {
                self.spl_token_owner_index.insert(owner_key, pubkey);
            }

            if account_indexes.contains(&AccountIndex::SplTokenMint)
                && let Some(mint_key) = G::unpack_account_mint(account_data)
                && account_indexes.include_key(mint_key)
            {
                self.spl_token_mint_index.insert(mint_key, pubkey);
            }
        }
    }
```

**File:** accounts-db/src/accounts_index.rs (L1391-1411)
```rust
            IndexLimit::Minimal
        } else {
            IndexLimit::InMemOnly
        };
        let index = AccountsIndex::<T, T>::new(&config, Arc::default());
        let mut gc = ReclaimsSlotList::new();

        match upsert_method {
            Some(upsert_method) => {
                // insert first entry for pubkey. This will use new_entry_after_update and not call update.
                index.upsert(slot0, slot0, &key, account_infos[0], &mut gc, upsert_method);
            }
            None => {
                let mut items = vec![(key, account_infos[0])];
                index.set_startup(Startup::Startup);
                let expected_len = items.len();
                let result = index.insert_new_if_missing_into_primary_index(slot0, &mut items);
                assert_eq!(result.count, expected_len);
                index.set_startup(Startup::Normal);
            }
        }
```

**File:** accounts-db/src/accounts_index/secondary.rs (L78-94)
```rust
#[derive(Debug, Default)]
pub struct RwLockSecondaryIndexEntry {
    account_keys: RwLock<HashSet<Pubkey>>,
}

impl SecondaryIndexEntry for RwLockSecondaryIndexEntry {
    fn insert_if_not_exists(&self, key: &Pubkey, inner_keys_count: &AtomicU64) {
        if self.account_keys.read().unwrap().contains(key) {
            // the key already exists, so nothing to do here
            return;
        }

        let was_newly_inserted = self.account_keys.write().unwrap().insert(*key);
        if was_newly_inserted {
            inner_keys_count.fetch_add(1, Ordering::Relaxed);
        }
    }
```

**File:** accounts-db/src/accounts_index/secondary.rs (L132-171)
```rust
    /// Inserts `inner_key` into `key`'s map.
    pub fn insert(&self, key: &Pubkey, inner_key: &Pubkey) {
        // Note: Always lock the reverse index first, so we synchronize with remove().
        // Pre-size to 1 to avoid push() over-allocating an empty Vec to capacity 4.
        let reverse_index_entry = self
            .reverse_index
            .entry(*inner_key)
            .or_insert_with(|| RwLock::new(Vec::with_capacity(1)));
        let mut outer_keys = reverse_index_entry.write().unwrap();

        // Now insert into the index.
        // Note, we do this get()-then-unwrap instead of calling entry() directly, because
        // get() is a read lock whereas entry() is a write lock.  We assume `key` already has
        // a map created, so optimize for the common case and only take a read lock.
        self.index
            .get(key)
            .unwrap_or_else(|| self.index.entry(*key).or_default().downgrade())
            .insert_if_not_exists(inner_key, &self.stats.num_inner_keys);

        if !outer_keys.contains(key) {
            outer_keys.push(*key);
        }

        // explicitly drop the locks so we don't hold them while reporting metrics
        drop(outer_keys);
        drop(reverse_index_entry);

        if self.stats.last_report.should_update(1000) {
            datapoint_info!(
                self.metrics_name,
                ("num_secondary_keys", self.index.len(), i64),
                (
                    "num_inner_keys",
                    self.stats.num_inner_keys.load(Ordering::Relaxed),
                    i64
                ),
                ("num_reverse_index_keys", self.reverse_index.len(), i64),
            );
        }
    }
```

**File:** accounts-db/src/accounts_index/secondary.rs (L212-250)
```rust
    /// Removes `inner_key` from the secondary index, if the closure `should_remove` returns true.
    ///
    /// `should_remove` is evaluated while holding `inner_key`'s reverse-index entry lock. Because
    /// `insert()` acquires that same lock before adding a mapping, holding it across the check
    /// serializes this removal against a concurrent `insert(_, inner_key)`. This only yields a
    /// correct decision if writers update the state that `should_remove` reads before calling
    /// `insert()`; otherwise the check can pass against stale state and remove a mapping that a
    /// concurrent writer expects to survive.
    pub fn remove_by_inner_key_if(&self, inner_key: &Pubkey, should_remove: impl Fn() -> bool) {
        // Note: Always lock the reverse-index first, so we synchronize with insert().
        let DashMapEntry::Occupied(reverse_index_entry) = self.reverse_index.entry(*inner_key)
        else {
            // if inner_key doesn't exist in the reverse-index, nothing to do here
            return;
        };

        // Re-check under the reverse-index entry lock. If the caller no longer wants the key
        // removed (e.g. it was concurrently re-added), leave its mapping in place.
        if !should_remove() {
            return;
        }

        // First go through the reverse-index and remove inner_key from all forward-indexes.
        let num_removed = reverse_index_entry
            .get()
            .write()
            .unwrap()
            .drain(..)
            .map(|outer_key| self.remove_index_entries(&outer_key, inner_key) as u64)
            .sum();

        // And now after removing inner_key from all forward-indexes,
        // remove its entry from the reverse-index.
        reverse_index_entry.remove();

        self.stats
            .num_inner_keys
            .fetch_sub(num_removed, Ordering::Relaxed);
    }
```

**File:** rpc/src/rpc.rs (L2310-2357)
```rust
    /// Get an iterator of spl-token accounts by owner address
    async fn get_filtered_spl_token_accounts_by_owner(
        &self,
        bank: Arc<Bank>,
        program_id: Pubkey,
        owner_key: Pubkey,
        mut filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> RpcCustomResult<Vec<(Pubkey, AccountSharedData)>> {
        // The by-owner accounts index checks for Token Account state and Owner address on
        // inclusion. However, due to the current AccountsDb implementation, an account may remain
        // in storage as a zero-lamport AccountSharedData::Default() after being wiped and reinitialized in
        // later updates. We include the redundant filters here to avoid returning these accounts.
        //
        // Filter on Token Account state
        filters.push(RpcFilterType::TokenAccountState);
        // Filter on Owner address
        filters.push(RpcFilterType::Memcmp(Memcmp::new_raw_bytes(
            SPL_TOKEN_ACCOUNT_OWNER_OFFSET,
            owner_key.to_bytes().into(),
        )));

        if self
            .config
            .account_indexes
            .contains(&AccountIndex::SplTokenOwner)
        {
            if !self.config.account_indexes.include_key(&owner_key) {
                return Err(RpcCustomError::KeyExcludedFromSecondaryIndex {
                    index_key: owner_key.to_string(),
                });
            }
            self.get_filtered_indexed_accounts(
                &bank,
                &IndexKey::SplTokenOwner(owner_key),
                &program_id,
                filters,
                sort_results,
            )
            .await
            .map_err(|e| RpcCustomError::ScanError {
                message: e.to_string(),
            })
        } else {
            self.get_filtered_program_accounts(bank, program_id, filters, sort_results)
                .await
        }
    }
```
