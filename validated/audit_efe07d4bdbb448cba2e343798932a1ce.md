### Title
Secondary-index `getProgramAccounts`/`getTokenAccountsBy{Owner,Mint}` allocates a `Vec<Pubkey>` for the *entire* index key before the `byte_limit_for_scan` abort check ever runs, letting attacker-controlled index fan-out drive per-request memory cost - (File: `accounts-db/src/accounts_db.rs`)

### Summary
`AccountsDb::index_scan_accounts` calls `self.accounts_index.get_index_key_pubkeys(&index_key)` which eagerly collects *all* pubkeys registered under a secondary-index key (`ProgramId`/`SplTokenMint`/`SplTokenOwner`) into a `Vec<Pubkey>` before iterating and before the scan's `byte_limit_for_scan` abort logic is ever consulted. Because the abort check (`config.is_aborted()`) only runs inside the loop, per-request memory for this allocation is proportional to the number of accounts an attacker has registered under a single index key, not to the RPC caller's requested/configured byte limit.

### Finding Description
`rpc.rs::get_filtered_indexed_accounts` (`rpc/src/rpc.rs:309-347`) forwards `self.config.scan_results_limit_bytes` into `Bank::get_filtered_indexed_accounts` [1](#0-0) , which forwards to `Accounts::load_by_index_key_with_filter` [2](#0-1) . That function builds a `ScanConfig` with `recreate_with_abort()` and relies on `config.abort()` being called once `accumulate_and_check_scan_result_size` reports the accumulated result size has exceeded `byte_limit_for_scan` [3](#0-2) .

That `config` (with the abort logic) is passed down into `AccountsDb::index_scan_accounts`:
```
for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
    if config.is_aborted() {
        break;
    }
    ...
}
``` [4](#0-3) 

The critical detail is that `get_index_key_pubkeys(&index_key)` is evaluated as the loop's iterator expression *before* the loop body (and thus before any `is_aborted()` check) executes even once. `AccountsIndex::get_index_key_pubkeys` simply delegates to the relevant `SecondaryIndex::get`, which does `inner_keys_map.keys()` [5](#0-4) [6](#0-5) , which materializes a full `Vec<Pubkey>` containing every account registered under that index key — with no participation from `byte_limit_for_scan` or `ScanConfig` at all.

Consequently, the size of this allocation (and the CPU/time to build it) is entirely driven by how many accounts the caller (an unprivileged user paying rent) has previously created with a given `owner` (for `AccountIndex::ProgramId`) or a given SPL token `mint`/`owner` (for `SplTokenMint`/`SplTokenOwner`), not by the `dataSlice`/byte-limit the RPC caller is relying on to bound cost. No existing guard (ancestor/root checks, zero-lamport filtering, `ScanGuard`) intervenes before this allocation — those all apply only to the subsequent per-pubkey `do_load` calls, which are bounded by the abort flag once set.

### Impact Explanation
This is a disproportionate memory/CPU cost issue: a single `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByMint` RPC call against a secondary-indexed key can force the node to allocate and populate a `Vec<Pubkey>` sized to the attacker's chosen fan-out under that key, even though the request specifies (or the node enforces) a small `byte_limit_for_scan`. This maps to the "disproportionate storage and CPU cost" scoped impact category — a single call, no multiple clients or repeated-call requirement needed to trigger the outsized allocation.

### Likelihood Explanation
Requires the RPC node operator to have enabled `AccountIndex::ProgramId`, `SplTokenMint`, or `SplTokenOwner` (a documented, commonly-used RPC configuration for indexed `getProgramAccounts` queries) — this is a stated precondition of the question, not something the attacker configures. Given that, the attacker only needs to be an ordinary account owner: create (and pay rent for) a large number of accounts owned by, or referencing, the same program/mint/owner pubkey, then issue one `getProgramAccounts`/`getTokenAccountsBy*` call. This is fully reproducible and requires no validator/leader/staked control.

### Recommendation
Bound the secondary-index pubkey materialization itself, e.g. by adding a streaming/iterator-based accessor to `SecondaryIndex` that yields pubkeys without a full upfront `Vec` allocation, or by capping/erroring on `get_index_key_pubkeys` when the index key's registered inner-key count exceeds a safe threshold before it is fully collected, so the `byte_limit_for_scan`/abort mechanism can influence work performed proportional to the allocation as well as the per-account loads.

### Proof of Concept
```rust
// accounts-db/src/accounts_index.rs (or a new integration test in accounts-db)
#[test]
fn test_get_index_key_pubkeys_allocation_independent_of_byte_limit() {
    let secondary_indexes = spl_token_mint_index_enabled(); // or program_id_index_enabled()
    let index = AccountsIndex::<bool, bool>::default_for_tests();
    let mint_key = Pubkey::new_unique();

    // Attacker registers N accounts all pointing at the same mint/index key.
    const N: usize = 50_000;
    for _ in 0..N {
        let account_key = Pubkey::new_unique();
        index.upsert(0, 0, &account_key, true, &mut ReclaimsSlotList::new(), UPSERT_RECLAIM_TEST_DEFAULT);
        index.update_secondary_indexes(
            &account_key,
            &make_token_account_with_mint(mint_key),
            &secondary_indexes,
        );
    }

    // Regardless of any caller-side byte_limit_for_scan, the pubkeys Vec allocated here
    // is O(N), materialized before any abort check can run.
    let pubkeys = index.get_index_key_pubkeys(&IndexKey::SplTokenMint(mint_key));
    assert_eq!(pubkeys.len(), N);
    assert!(pubkeys.capacity() * std::mem::size_of::<Pubkey>() >= N * std::mem::size_of::<Pubkey>());
}
```
Expected assertion: `pubkeys.len()`/allocation size scales linearly with `N` (attacker-controlled fan-out) and is unaffected by any `byte_limit_for_scan` value passed to `load_by_index_key_with_filter`/`get_filtered_indexed_accounts`, demonstrating the allocation happens prior to, and independent of, the scan's byte-limit abort mechanism.

### Citations

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

**File:** runtime/src/bank.rs (L5134-5147)
```rust
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

**File:** accounts-db/src/accounts_index/secondary.rs (L252-258)
```rust
    pub fn get(&self, key: &Pubkey) -> Vec<Pubkey> {
        if let Some(inner_keys_map) = self.index.get(key) {
            inner_keys_map.keys()
        } else {
            vec![]
        }
    }
```
