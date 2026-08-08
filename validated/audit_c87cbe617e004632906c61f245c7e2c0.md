### Title
Unbounded growth of secondary-index entries (ProgramId / SplTokenMint / SplTokenOwner) causes disproportionate CPU/IO cost per RPC scan - ([File: accounts-db/src/accounts_index/secondary.rs])

### Summary
The external report describes a DoS caused by an attacker cheaply inflating an array that is capped (`MAX_DELEGATES = 1024`), but whose *processing* cost is unbounded relative to the fixed gas budget of a block. The analogous condition exists in agave's `AccountsDb` secondary indexes (`ProgramId`, `SplTokenMint`, `SplTokenOwner`): any unprivileged user can insert an unbounded number of entries under a single index key (e.g. one mint or one owner pubkey), and there is no cap analogous to `MAX_DELEGATES`. A single downstream operation that consumes that key (`index_scan_accounts`) must then load every entry synchronously, producing disproportionate CPU/IO cost concentrated in one call.

### Finding Description
`SecondaryIndex::insert()` inserts an `inner_key` (an account pubkey) into the `HashSet` kept in `RwLockSecondaryIndexEntry` under an `outer_key` (a program id, mint, or token owner), with no maximum size check: [1](#0-0) 

Any unprivileged wallet can create arbitrarily many SPL token accounts that all share the same `mint` or `owner` field (this only costs the token-account rent-exempt minimum per account, no privileged role required), directly growing the `HashSet` for one index key without limit, unlike the `MAX_DELEGATES` cap in the reported bug class.

When a caller resolves that index key (e.g., via `getTokenAccountsByOwner`, `getTokenAccountsByMint`, or `getProgramAccounts` with an owner/mint filter), `AccountsDb::index_scan_accounts` pulls *all* pubkeys registered under that key and loads each one from storage in a single synchronous loop: [2](#0-1) 

This is reached from the RPC layer in a single call via `get_filtered_indexed_accounts` → `Bank::get_filtered_indexed_accounts` → `AccountsDb::load_by_index_key_with_filter`: [3](#0-2) [4](#0-3) 

Because the secondary index has no per-key cap, the number of `do_load` calls in `index_scan_accounts`'s loop is entirely attacker-controlled and grows linearly with the number of token accounts the attacker chooses to create under one mint/owner, with no upper bound comparable to `MAX_DELEGATES`.

### Impact Explanation
A single RPC request against an attacker-inflated secondary-index key forces the validator/RPC node to synchronously load an attacker-chosen, unbounded number of accounts from storage inside one scan (guarded only by a `ScanGuard`, not by a result-count or entry-count limit at the index level). This causes disproportionate CPU and storage I/O cost for a single call relative to the low cost the attacker paid to create the underlying accounts — directly matching the reported bug class (cheap unbounded insertion into an attacker-influenced collection that a victim operation must fully traverse).

### Likelihood Explanation
Likelihood is Low-to-Medium: this only applies to validators/RPC nodes that have secondary indexes enabled (`--account-index program-id|spl-token-mint|spl-token-owner`), which is opt-in and not the default configuration. Where enabled, the attack requires no privileged role, only the cost of creating many token accounts under one mint/owner.

### Recommendation
Introduce a configurable maximum number of inner keys per secondary-index outer key (analogous to reducing `MAX_DELEGATES`), rejecting or evicting inserts beyond the cap, and/or enforce a hard result-count/time budget in `index_scan_accounts`'s per-pubkey load loop so that a single scan cannot be forced to perform unbounded work regardless of how large an attacker has grown one index key.

### Proof of Concept
1. Enable a secondary index (e.g. `--account-index spl-token-owner`).
2. As any unprivileged wallet, create a very large number of SPL token accounts that all set the same `owner` field (paying only the token-account rent-exempt minimum each time); each insert goes through `update_secondary_indexes` → `SecondaryIndex::insert` with no cap check, as shown in `secondary.rs:132-153`.
3. Issue a single `getTokenAccountsByOwner` RPC call for that owner. This resolves to `index_scan_accounts` (`accounts_db.rs:3398-3410`), which iterates every attacker-created pubkey and performs a `do_load` for each synchronously in the calling thread, producing CPU/IO cost proportional to the attacker-controlled count rather than any fixed bound.

### Citations

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

**File:** rpc/src/rpc.rs (L309-341)
```rust
    pub async fn get_filtered_indexed_accounts(
        &self,
        bank: &Arc<Bank>,
        index_key: &IndexKey,
        program_id: &Pubkey,
        filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let bank = Arc::clone(bank);
        let index_key = index_key.to_owned();
        let program_id = program_id.to_owned();
        let byte_limit_for_scans = self.config.scan_results_limit_bytes;
        let mut accounts = self
            .runtime
            .spawn_blocking(move || {
                bank.get_filtered_indexed_accounts(
                    &index_key,
                    |account| {
                        // The program-id account index checks for Account owner on inclusion.
                        // However, due to the current AccountsDb implementation, an account may
                        // remain in storage as a zero-lamport AccountSharedData::Default() after
                        // being wiped and reinitialized in later updates. We include the redundant
                        // filters here to avoid returning these accounts.
                        account.owner().eq(&program_id)
                            && filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                    },
                    byte_limit_for_scans,
                )
            })
            .await
            .expect("Failed to spawn blocking task")?;
```

**File:** rpc/src/rpc.rs (L2310-2356)
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
```
