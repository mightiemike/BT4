### Title
Unbounded `Vec<Pubkey>` allocation in `get_index_key_pubkeys` bypasses `byte_limit_for_scan` before any abort check - ([File: accounts-db/src/accounts_index.rs])

### Summary
`index_scan_accounts` calls `self.accounts_index.get_index_key_pubkeys(&index_key)` to materialize the *entire* list of pubkeys registered under a secondary index key into a new `Vec<Pubkey>` before the per-account `byte_limit_for_scan` abort check is ever consulted. An attacker who fans out many accounts under a single indexed key (same `owner` for `AccountIndex::ProgramId`, or same mint/owner for SPL secondary indexes) forces this allocation and the following `do_load` iteration to scale with total registered accounts under that key, not with the caller's requested/expected byte limit.

### Finding Description
The RPC entrypoint `get_filtered_program_accounts` / `get_filtered_spl_token_accounts_by_owner` / `get_filtered_spl_token_accounts_by_mint` in `rpc/src/rpc.rs` call `JsonRpcRequestProcessor::get_filtered_indexed_accounts` [1](#0-0) , which forwards to `Bank::get_filtered_indexed_accounts` [2](#0-1) , which calls `Accounts::load_by_index_key_with_filter` [3](#0-2) . This sets up an abortable `ScanConfig` and byte-accumulator (`accumulate_and_check_scan_result_size`), then delegates to `AccountsDb::index_scan_accounts`.

Inside `index_scan_accounts`:
```
for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
    if config.is_aborted() {
        break;
    }
    ...
}
``` [4](#0-3) 

`get_index_key_pubkeys` itself unconditionally builds a fresh `Vec<Pubkey>` for the entire secondary index bucket:
```
pub(crate) fn get_index_key_pubkeys(&self, index_key: &IndexKey) -> Vec<Pubkey> {
    match index_key {
        IndexKey::ProgramId(key) => self.program_id_index.get(key),
        ...
    }
}
``` [5](#0-4) 

and `SecondaryIndex::get` materializes all inner keys via `inner_keys_map.keys()`:
```
pub fn get(&self, key: &Pubkey) -> Vec<Pubkey> {
    if let Some(inner_keys_map) = self.index.get(key) {
        inner_keys_map.keys()
    } else {
        vec![]
    }
}
``` [6](#0-5) 

This allocation and enumeration is performed once, in full, as the argument to the `for` loop, *before* `config.is_aborted()` is checked for the first time. `byte_limit_for_scan`/`ScanConfig::abort()` can only stop the loop from calling `do_load` on *subsequent* pubkeys already contained in the fully-materialized `Vec`; it cannot prevent the initial `O(N)` allocation and copy of all `N` pubkeys registered under that index key, where `N` is attacker-controlled fan-out (number of accounts sharing the same owner/mint/owner-of-token-account under the secondary index).

An unprivileged user can drive `N` arbitrarily high simply by creating many accounts owned by the same program (or many SPL token accounts sharing a mint/owner), all of which they pay rent/creation cost for — no validator, leader, or privileged control is required. Nothing in `include_key`, `ScanGuard`, or ancestor checks bounds `N` before the `Vec` is built; those checks only gate whether the secondary index path is used at all, not the size of one bucket.

### Impact Explanation
Each `getProgramAccounts` (or SPL token by-owner/by-mint) RPC call against an indexed key causes the validator's RPC-serving thread to allocate a `Vec<Pubkey>` (32 bytes/pubkey plus `DashMap`/`SecondaryIndexEntry` overhead) proportional to the total accounts under that key, even though the caller supplied a `dataSlice`/`scan_results_limit_bytes` byte limit intending to bound per-request cost (`self.config.scan_results_limit_bytes` in `rpc/src/rpc.rs`). This is a disproportionate memory/CPU cost relative to caller-requested and expected bounded work — the invariant that per-request work should be proportional to allowed scan cost, not attacker-controlled key fan-out, is violated. This falls under the "disproportionate storage and CPU cost" / RPC resource-exhaustion category, and is triggerable with a single `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByMint` call once the secondary index has been populated by prior unprivileged account creation.

### Likelihood Explanation
Requires `AccountIndex::ProgramId`, `SplTokenMint`, or `SplTokenOwner` secondary index to be enabled on the RPC node (a common configuration for nodes offering `getProgramAccounts`-style queries). Given that, any unprivileged user can create/rewrite many accounts sharing one owner/mint/owner-of-token key over time (no elevated privileges needed — just paying rent for account creation), then issue a single `getProgramAccounts` call to trigger the full-bucket allocation regardless of the byte limit. This is fully reproducible and requires only one RPC call once the index bucket is populated.

### Recommendation
Bound the enumeration of the secondary index bucket itself instead of only gating `do_load` calls after full materialization: either (a) have `SecondaryIndex::get`/`get_index_key_pubkeys` accept a max-count or early-abort predicate so it can early-return once enough bytes/entries are collected, or (b) iterate the underlying map lazily (e.g., via an iterator over `DashMap` entries) and check `config.is_aborted()` / an entry-count cap before appending each pubkey to the result, rather than eagerly cloning the entire keys collection up front.

### Proof of Concept
```rust
// accounts-db/src/accounts_index.rs (test)
#[test]
fn test_get_index_key_pubkeys_scales_with_total_registered_not_byte_limit() {
    let secondary_indexes = program_id_index_enabled(); // AccountIndex::ProgramId
    let index = AccountsIndex::<bool, bool>::default_for_tests();
    let program_id = Pubkey::new_unique();

    // Attacker registers many accounts under the same owner/program id.
    const N: usize = 100_000;
    for _ in 0..N {
        let account_key = Pubkey::new_unique();
        index.upsert(0, 0, &account_key, true, &mut ReclaimsSlotList::new(), UPSERT_RECLAIM_TEST_DEFAULT);
        index.update_secondary_indexes(
            &account_key,
            &AccountSharedData::new(1, 0, &program_id),
            &secondary_indexes,
        );
    }

    // Even though a caller-facing byte_limit_for_scan would only allow ~1 account's worth
    // of bytes, get_index_key_pubkeys allocates the FULL Vec<Pubkey> of size N up front.
    let pubkeys = index.get_index_key_pubkeys(&IndexKey::ProgramId(program_id));
    assert_eq!(pubkeys.len(), N); // allocation size == total registered, not bounded by any limit
}
```
Companion integration test at the `AccountsDb::index_scan_accounts` level should assert that `do_load` is invoked (or at minimum the input `Vec` produced by `get_index_key_pubkeys`) has length `N` regardless of a very small `byte_limit_for_scan` passed to `Accounts::load_by_index_key_with_filter`, demonstrating the abort only trims the loop's `do_load` calls, not the initial allocation/enumeration cost.

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
