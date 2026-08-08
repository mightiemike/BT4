I have enough context now to finalize the analysis. This confirms `SecondaryIndex::insert` at accounts-db/src/accounts_index/secondary.rs stores an unbounded `HashSet<Pubkey>` per outer key with no size cap, and `get_index_key_pubkeys`/`index_scan_accounts`/`get_filtered_indexed_accounts` iterate the entire set on every scan.

### Title
Unbounded secondary-index fan-out per key lets any unprivileged account-creator inflate per-key scan cost without bound - (File: accounts-db/src/accounts_index/secondary.rs)

### Summary
The external report describes a delegated contractor who can cheaply add an unbounded number of "tasks" to a structure that a core function (`allocateFunds`) must then iterate in full, with no builder-side mechanism to cap or remove the bloat. The agave analog is the `AccountsIndex` secondary index (`AccountIndex::ProgramId` / `SplTokenMint` / `SplTokenOwner`): any unprivileged user can create arbitrarily many accounts sharing the same owner/mint/authority key, and `SecondaryIndex::insert` [1](#0-0)  appends every one of them into a single unbounded `HashSet<Pubkey>` behind that one outer key, with no per-key limit anywhere in the insert path.

### Finding Description
`update_secondary_indexes` inserts a pubkey into the `program_id_index`/`spl_token_mint_index`/`spl_token_owner_index` for every account write whose owner/mint/authority is not excluded [2](#0-1) . The underlying `SecondaryIndex::insert` grows the entry's `RwLockSecondaryIndexEntry::account_keys` `HashSet<Pubkey>` without any cap on size [3](#0-2) [1](#0-0) . Since any unprivileged transaction can create a new account and choose its owner/mint (e.g. an SPL token account under an attacker-controlled or well-known mint/owner), an attacker can grow a single index key's set to contain millions of pubkeys, all cheaply (bounded only by normal account rent/fees, not by any structural limit tied to the size already accumulated under that one key).

That single bloated key is then read in its entirety, unbounded, by consumers: `get_index_key_pubkeys` returns the whole `Vec<Pubkey>` for the key [4](#0-3) , which `index_scan_accounts` iterates fully and calls `do_load` for every pubkey [5](#0-4) , and `Bank::get_program_accounts`/`get_filtered_indexed_accounts` expose this to callers with `byte_limit_for_scan` being optional (`None` in `get_program_accounts`) [6](#0-5) .

This mirrors the referenced bug class precisely: a cheap, permission-light action (adding a "task"/account) inflates a shared structure that a core function must fully process, and there is no mechanism analogous to "update `lastAllocatedTask`" or "remove the malicious contractor" to bound or evict entries from a single secondary-index key once it has grown large — the only bound is `AccountSecondaryIndexesIncludeExclude`, which is an operator-configured allow/deny list set at validator startup [7](#0-6) , not something the system enforces per key automatically at insert time.

### Impact Explanation
Every scan against a bloated index key (`index_scan_accounts`, `get_filtered_indexed_accounts`, `get_program_accounts`) pays CPU and I/O cost proportional to however many pubkeys an attacker chose to accumulate under that key, which is disproportionate to the cost the attacker incurred to create them (ordinary account-creation fees/rent, paid once, vs. repeated O(n) scan cost on every subsequent call). This is a disproportionate CPU/storage cost issue rather than a data-correctness one; it does not corrupt balances or hashes.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires enabling secondary indexes (`--account-indexes`), which is an operator opt-in feature and not the default validator configuration, and the strongest realizations of the impact are through the RPC-facing `get_program_accounts`/`get_filtered_indexed_accounts` paths, which is explicitly listed as an excluded/lower-priority area in scope guidance unless reachable at low call cost. Internally, `index_scan_accounts` is also used by non-RPC consumers, but the primary attacker-controllable amplification vector remains the optional secondary index feature.

### Recommendation
Consider bounding the number of inner keys tracked per outer secondary-index key (with metrics/alerting when a key approaches the bound), and/or enforcing `byte_limit_for_scan`-style limits uniformly across all consumers of `get_index_key_pubkeys`/`index_scan_accounts`, including `Bank::get_program_accounts`, so a single attacker-inflated key cannot force an unbounded amount of work per call.

### Proof of Concept
1. Start a validator/test harness with `AccountSecondaryIndexes { indexes: {AccountIndex::ProgramId}, ... }` enabled and no include/exclude filter.
2. From an unprivileged client, repeatedly create new accounts (or SPL token accounts) all owned by the same `program_id` (or same mint/owner for the SPL indexes), e.g. via the pattern in `test_get_filtered_indexed_accounts` [8](#0-7) , scaled to a large N.
3. Call `bank.get_program_accounts(&program_id)` (no byte limit available) [9](#0-8)  or `get_filtered_indexed_accounts` without a `byte_limit_for_scan`; observe that scan time/CPU grows linearly with N with no structural cap, unlike `load_by_index_key_with_filter`'s optional abort path which is opt-in per caller, not enforced at the index layer [10](#0-9) .

### Citations

**File:** accounts-db/src/accounts_index/secondary.rs (L37-41)
```rust
#[derive(Debug, PartialEq, Eq, Clone)]
pub struct AccountSecondaryIndexesIncludeExclude {
    pub exclude: bool,
    pub keys: HashSet<Pubkey>,
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

**File:** accounts-db/src/accounts_index/secondary.rs (L132-153)
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
```

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

**File:** accounts-db/src/accounts_index.rs (L614-631)
```rust
    pub(crate) fn update_secondary_indexes(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        account_indexes: &AccountSecondaryIndexes,
    ) {
        if account_indexes.is_empty() {
            return;
        }

        let account_owner = account.owner();
        let account_data = account.data();

        if account_indexes.contains(&AccountIndex::ProgramId)
            && account_indexes.include_key(account_owner)
        {
            self.program_id_index.insert(account_owner, pubkey);
        }
```

**File:** accounts-db/src/accounts_db.rs (L3398-3410)
```rust
        for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
            if config.is_aborted() {
                break;
            }
            if let Some((account, slot)) = self.do_load(
                ancestors,
                &pubkey,
                LoadHint::Unspecified,
                PopulateReadCache::False,
            ) {
                scan_func(Some((&pubkey, account, slot)));
            }
        }
```

**File:** runtime/src/bank.rs (L5112-5147)
```rust
    pub fn get_program_accounts(
        &self,
        program_id: &Pubkey,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        self.rc
            .accounts
            .load_by_program(&self.ancestors, self.bank_id, program_id)
    }

    pub fn get_filtered_program_accounts<F: Fn(&AccountSharedData) -> bool>(
        &self,
        program_id: &Pubkey,
        filter: F,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        self.rc.accounts.load_by_program_with_filter(
            &self.ancestors,
            self.bank_id,
            program_id,
            filter,
        )
    }

    pub fn get_filtered_indexed_accounts<F: Fn(&AccountSharedData) -> bool>(
        &self,
        index_key: &IndexKey,
        filter: F,
        byte_limit_for_scan: Option<usize>,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        self.rc.accounts.load_by_index_key_with_filter(
            &self.ancestors,
            self.bank_id,
            index_key,
            filter,
            byte_limit_for_scan,
        )
    }
```

**File:** runtime/src/bank/tests.rs (L3504-3528)
```rust
#[test]
fn test_get_filtered_indexed_accounts() {
    let (genesis_config, _mint_keypair) = create_genesis_config(500);
    let mut account_indexes = AccountSecondaryIndexes::default();
    account_indexes.indexes.insert(AccountIndex::ProgramId);
    let bank_config = BankTestConfig {
        accounts_db_config: AccountsDbConfig {
            account_indexes: Some(account_indexes),
            ..ACCOUNTS_DB_CONFIG_FOR_TESTING
        },
    };
    let (bank, bank_forks) =
        Bank::new_with_paths_for_tests(&genesis_config, Some(bank_config), vec![], None)
            .wrap_with_bank_forks_for_tests();

    let address = Pubkey::new_unique();
    let program_id = Pubkey::new_unique();
    let account = AccountSharedData::new(1, 0, &program_id);
    bank.store_account(&address, &account);

    let indexed_accounts = bank
        .get_filtered_indexed_accounts(&IndexKey::ProgramId(program_id), |_| true, None)
        .unwrap();
    assert_eq!(indexed_accounts.len(), 1);
    assert_eq!(indexed_accounts[0], (address, account));
```

**File:** accounts-db/src/accounts.rs (L396-433)
```rust
    pub fn load_by_index_key_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        index_key: &IndexKey,
        filter: F,
        byte_limit_for_scan: Option<usize>,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let sum = AtomicUsize::default();
        let config = ScanConfig::default().recreate_with_abort();
        let mut collector = Vec::new();
        let result = self
            .accounts_db
            .index_scan_accounts(
                ancestors,
                bank_id,
                *index_key,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        let use_account = filter(account);
                        if use_account
                            && Self::accumulate_and_check_scan_result_size(
                                &sum,
                                account,
                                &byte_limit_for_scan,
                            )
                        {
                            // total size of results exceeds size limit, so abort scan
                            config.abort();
                        }
                        use_account
                    });
                },
                &config,
            )
            .map(|_| collector);
        Self::maybe_abort_scan(result, &config)
    }
```
