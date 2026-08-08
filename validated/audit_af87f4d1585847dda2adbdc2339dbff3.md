## Finding

An unprivileged user can permanently bloat the `SplTokenOwner` (and `SplTokenMint`) secondary index maintained by any validator/RPC node that has this optional index enabled, by repeatedly calling the standard, unrestricted SPL Token `SetAuthority` instruction to change a token account's owner field. Every such change adds a new, never-cleaned entry into the index without removing the stale one, causing unbounded memory growth and increasingly expensive linear scans — the same "any user repeatedly calls an unprotected function that appends to a shared list, growing cost for related operations forever" pattern as the Basis `setMinimumDistribution` bug.

### Root cause

`AccountsIndex::update_secondary_indexes` is invoked on every account update and unconditionally calls `update_spl_token_secondary_indexes`, which inserts the token account's *current* owner/mint key into the `spl_token_owner_index`/`spl_token_mint_index` whenever the account is owned by the token program: [1](#0-0) 

`SecondaryIndex::insert` appends the new outer key (`owner_key`) to the account's `reverse_index` Vec entry and creates/keeps a forward `index` entry under that owner key — but there is no code path that removes the *previous* owner's forward-index entry when the account's owner changes; removal (`purge_secondary_indexes_by_inner_key_if` / `remove_by_inner_key_if`) is only triggered when the account itself dies (`handle_dead_keys`), never on a mere field update: [2](#0-1) [3](#0-2) 

The reverse-index entry is explicitly documented as expected to stay small ("this is rare"), but nothing enforces that: [4](#0-3) 

Because `insert()` does a linear `outer_keys.contains(key)` scan of this Vec on every call, an account with `N` historical owners makes each subsequent `SetAuthority` call take `O(N)` just for the secondary-index update, on top of leaving `N` permanent, un-reclaimable entries in the DashMap-backed forward `index`.

The index defaults to including every key unless a validator operator explicitly configures include/exclude lists — i.e. by default all created keys are tracked with no bound: [5](#0-4) [6](#0-5) 

### Impact

Any unprivileged wallet can, at negligible cost (just compute-unit fees for `SetAuthority`, no incremental rent since account size/lamports never change), grow the `spl_token_owner_index`/`spl_token_mint_index` structures without bound on any node that enables `--account-index spl-token-owner`/`spl-token-mint` (a common configuration for RPC/indexing nodes). This causes:
- Disproportionate, permanent memory growth in `AccountsIndex` (the secondary index DashMaps never shrink for a live account, regardless of how many times its owner authority changed).
- Disproportionate CPU cost: `insert()`'s linear scan makes each further authority change on the same account progressively slower, and downstream consumers like `get_filtered_spl_token_accounts_by_owner`, `log_secondary_indexes`, and scans iterating `get_index_key_pubkeys` become slower/larger as stale owner buckets accumulate garbage entries that are never purged until the account itself is closed.

This is directly analogous to the reported Basis bug: an unauthenticated actor repeatedly invokes a legitimate, callable operation (`SetAuthority` vs. `setMinimumDistribution`) that appends to a shared index/list with no access control and no compaction, permanently raising the cost of all subsequent operations touching that structure.

### Likelihood

Requires only: (1) the target validator/RPC operator to have enabled the `spl-token-owner` or `spl-token-mint` secondary index (a documented, non-default but commonly used RPC feature), and (2) the attacker to own a normal SPL token account and repeatedly call `SetAuthority` — no special privilege needed. This is a cheap, entirely permissionless action reachable via ordinary token-program instructions.

### Recommendation

- Deduplicate/replace rather than append when a `key -> inner_key` mapping's outer key changes: when `update_spl_token_secondary_indexes` observes the account's owner/mint field changed, explicitly remove the stale forward/reverse entry for the old value before inserting the new one, instead of relying solely on account-death cleanup.
- Consider bounding or evicting per-account owner history in `SecondaryReverseIndexEntry`, or switching to a data structure that overwrites rather than accumulates stale keys, since the "should be rare" assumption backing the `Vec` choice is not enforced and can be violated by a user in a tight loop.

### Proof of Concept

1. Start a validator/RPC node with `--account-index spl-token-owner` enabled (no include/exclude key filter).
2. Create an SPL token account (`InitializeAccount`).
3. In a loop, call `SetAuthority` (AuthorityType::AccountOwner) to set the account's owner authority to a freshly generated pubkey each iteration, then confirm the transaction.
4. Observe via `AccountsIndex::get_index_key_size(&AccountIndex::SplTokenOwner, ...)` (or `log_secondary_indexes`) that the forward index accumulates one live entry per distinct owner ever used, and that the account's `reverse_index` Vec (in `secondary.rs`) grows by one entry per iteration — none of which are ever purged as long as the account stays open, while each subsequent call incurs an `O(N)` `Vec::contains` scan in `SecondaryIndex::insert`. [7](#0-6)

### Citations

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

**File:** accounts-db/src/accounts_index.rs (L582-596)
```rust
    pub fn get_index_key_size(&self, index: &AccountIndex, index_key: &Pubkey) -> Option<usize> {
        match index {
            AccountIndex::ProgramId => self.program_id_index.index.get(index_key).map(|x| x.len()),
            AccountIndex::SplTokenOwner => self
                .spl_token_owner_index
                .index
                .get(index_key)
                .map(|x| x.len()),
            AccountIndex::SplTokenMint => self
                .spl_token_mint_index
                .index
                .get(index_key)
                .map(|x| x.len()),
        }
    }
```

**File:** accounts-db/src/accounts_index.rs (L856-877)
```rust
    /// Purges `inner_key` from each enabled secondary index
    pub(crate) fn purge_secondary_indexes_by_inner_key_if(
        &self,
        inner_key: &Pubkey,
        account_indexes: &AccountSecondaryIndexes,
        should_remove: impl Fn() -> bool,
    ) {
        if account_indexes.contains(&AccountIndex::ProgramId) {
            self.program_id_index
                .remove_by_inner_key_if(inner_key, &should_remove);
        }

        if account_indexes.contains(&AccountIndex::SplTokenOwner) {
            self.spl_token_owner_index
                .remove_by_inner_key_if(inner_key, &should_remove);
        }

        if account_indexes.contains(&AccountIndex::SplTokenMint) {
            self.spl_token_mint_index
                .remove_by_inner_key_if(inner_key, &should_remove);
        }
    }
```

**File:** accounts-db/src/accounts_index/secondary.rs (L22-35)
```rust
impl AccountSecondaryIndexes {
    pub fn is_empty(&self) -> bool {
        self.indexes.is_empty()
    }
    pub fn contains(&self, index: &AccountIndex) -> bool {
        self.indexes.contains(index)
    }
    pub fn include_key(&self, key: &Pubkey) -> bool {
        match &self.keys {
            Some(options) => options.exclude ^ options.keys.contains(key),
            None => true, // include all keys
        }
    }
}
```

**File:** accounts-db/src/accounts_index/secondary.rs (L57-61)
```rust
// The only cases where an inner key should map to a different outer key is
// if the key had different account data for the indexed key across different
// slots. As this is rare, it should be ok to use a Vec here over a HashSet, even
// though we are running some key existence checks.
type SecondaryReverseIndexEntry = RwLock<Vec<Pubkey>>;
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

**File:** validator/src/commands/run/args/account_secondary_indexes.rs (L11-59)
```rust
impl FromClapArgMatches for AccountSecondaryIndexes {
    fn from_clap_arg_match(matches: &ArgMatches) -> Result<Self> {
        let account_indexes: HashSet<AccountIndex> = matches
            .values_of("account_indexes")
            .unwrap_or_default()
            .map(|value| match value {
                "program-id" => AccountIndex::ProgramId,
                "spl-token-mint" => AccountIndex::SplTokenMint,
                "spl-token-owner" => AccountIndex::SplTokenOwner,
                _ => unreachable!(),
            })
            .collect();

        let account_indexes_include_keys: HashSet<Pubkey> =
            values_t!(matches, "account_index_include_key", Pubkey)
                .unwrap_or_default()
                .iter()
                .cloned()
                .collect();

        let account_indexes_exclude_keys: HashSet<Pubkey> =
            values_t!(matches, "account_index_exclude_key", Pubkey)
                .unwrap_or_default()
                .iter()
                .cloned()
                .collect();

        let exclude_keys = !account_indexes_exclude_keys.is_empty();
        let include_keys = !account_indexes_include_keys.is_empty();

        let keys = if !account_indexes.is_empty() && (exclude_keys || include_keys) {
            let account_indexes_keys = AccountSecondaryIndexesIncludeExclude {
                exclude: exclude_keys,
                keys: if exclude_keys {
                    account_indexes_exclude_keys
                } else {
                    account_indexes_include_keys
                },
            };
            Some(account_indexes_keys)
        } else {
            None
        };

        Ok(AccountSecondaryIndexes {
            keys,
            indexes: account_indexes,
        })
    }
```
