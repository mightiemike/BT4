## Confirmed root cause

The `SecondaryIndex::reverse_index` in `accounts-db/src/accounts_index/secondary.rs` maps each account pubkey ("inner key") to a `Vec<Pubkey>` of every distinct secondary-index value (mint/owner/program id) that account's data has ever contained across slots, and this `Vec` is grown via an `O(n)` linear `contains()` scan on every `insert()` call, with no bound on how large it can get before a matching root is cleaned. [1](#0-0) [2](#0-1) 

### Title
Unbounded, linearly-searched `Vec` in `SecondaryIndex::reverse_index` causes disproportionate per-write CPU cost when secondary account indexes are enabled - (File: `accounts-db/src/accounts_index/secondary.rs`)

### Summary
`SecondaryIndex` (used for the `--account-index program-id|spl-token-mint|spl-token-owner` feature) stores a reverse mapping from an account pubkey to all distinct secondary-index key values (e.g. mint addresses) that account's data has held across slots, using `type SecondaryReverseIndexEntry = RwLock<Vec<Pubkey>>`. On every account write, `insert()` performs `outer_keys.contains(key)` — a linear scan — before pushing a new value, exactly the "unbounded array + linear search" anti-pattern from the report. The comment in the code even documents that this behavior is assumed "rare," an assumption an adversarial account owner (any program that owns the account, since `owner()`/token-account layout fields are attacker-controlled data) can violate.

### Finding Description
`update_secondary_indexes()` is invoked on every account store when `account_indexes` is non-empty, once per touched account, both on the hot cache-write path and on flush: [3](#0-2) [4](#0-3) 

For SPL-token-like accounts, if the bytes at the mint/owner offset differ from the previously-recorded value, `update_spl_token_secondary_indexes` calls `self.spl_token_mint_index.insert(mint_key, pubkey)` (or the owner index) with a *new* `mint_key`: [5](#0-4) 

Inside `SecondaryIndex::insert`, the reverse-index entry for that account pubkey is a plain `Vec<Pubkey>`, and every insertion does a linear `contains()` check before `push()`: [6](#0-5) 

The comment explicitly states the design assumption: "The only cases where an inner key should map to a different outer key is if the key had different account data for the indexed key across different slots. As this is rare, it should be ok to use a Vec here over a HashSet." The existing regression test confirms the mechanism: writing distinct mint bytes for the *same account* in the *same slot* (or successive slots) creates one new reverse-index entry per distinct value observed, and these entries are only pruned when the account is fully cleaned/dead (`clean_rooted_entries`/`purge_secondary_indexes_by_inner_key_if`), not opportunistically as new values are written: [7](#0-6) 

Because the indexed field (mint/owner bytes, or the account owner for the `ProgramId` index) is fully attacker-controlled account data, an account owned by an attacker's own program can be rewritten with a different, unique 32-byte value in the indexed field on every slot it is touched, before the account is ever cleaned/rooted away. Each such write appends a new distinct entry to `reverse_index[pubkey]`, and the cost of every subsequent `insert()` for that same pubkey grows linearly with the list's length — i.e., processing writes to this one account becomes O(n²) in the number of distinct values written, all while holding the DashMap shard lock for that reverse-index entry.

### Impact Explanation
This affects `AccountsDB` secondary-index maintenance (opt-in via `--account-index`), a facility commonly enabled on RPC/indexer nodes. A single account, cheaply and repeatedly touched by an attacker-controlled program across slots with a unique "mint"/"owner" value each time, can drive its reverse-index `Vec` to unbounded size, causing:
- Disproportionate CPU cost on every subsequent write that touches the same account (quadratic blow-up), on the account-write hot path (`update_index_for_flush` / `update_secondary_index_cached_accounts`).
- Unbounded memory growth in the `reverse_index` DashMap entry, since entries are removed only when the whole account is dead/cleaned, not as stale values accumulate.

This matches the report's exact bug class (unbounded array + linear search, no policy preventing both at once) and produces the accepted impact category "disproportionate storage and CPU cost."

### Likelihood Explanation
Likelihood is real but scoped: it requires the validator/node operator to have enabled a secondary account index (`--account-index program-id|spl-token-mint|spl-token-owner`), which is not the default configuration and is typically enabled on RPC-serving nodes rather than plain validators. Any unprivileged user who can deploy a program and repeatedly write differing "token account"-shaped data (owned by the SPL Token / Token-2022 program id, or any program, for the `ProgramId` index) into the same account across many slots can trigger the growth without any special privilege — no consensus or validator role is needed.

### Recommendation
- Replace `SecondaryReverseIndexEntry`'s `Vec<Pubkey>` with a `HashSet<Pubkey>` (matching what `RwLockSecondaryIndexEntry` already does for the forward index), eliminating the O(n) `contains()`/search cost regardless of how many distinct values accumulate.
- Additionally, bound the number of distinct secondary-index values retained per account (e.g., cap at a small constant, evicting the oldest when exceeded), since legitimately only the latest value matters for scans; alternatively, proactively prune stale reverse-index values when a newer value for the same account is written in the same or later slot rather than deferring cleanup entirely to account-level death/`clean`.
- Add a metric/alert on reverse-index entry length outliers to detect this pattern in production before it becomes a resource issue.

### Proof of Concept
1. Run a node with `--account-index spl-token-mint` (or `program-id`) enabled.
2. Deploy/own an account with `owner == spl_token::id()` (or Token-2022 id).
3. In a loop, submit transactions in successive slots that overwrite the account's mint-offset bytes with a fresh, never-before-used 32-byte value each time (staying within `spl_generic_token::token::Account`'s packed layout and required "initialized" byte).
4. Do not let the account become dead/rooted-and-cleaned (keep it referenced/alive, e.g. by repeatedly touching it before any long enough gap for `clean` to purge it).
5. Observe `SecondaryIndex::reverse_index.get(&account_key)`'s `Vec` length grow without bound (as directly exercised by the existing test `run_test_secondary_indexes_same_slot_and_forks` at accounts-db/src/accounts_index.rs:2352-2454, which demonstrates two distinct values accumulating for one account/slot) and measure `insert()`/`update_secondary_indexes` latency for that pubkey increasing linearly with each additional distinct value.

### Citations

**File:** accounts-db/src/accounts_index/secondary.rs (L57-61)
```rust
// The only cases where an inner key should map to a different outer key is
// if the key had different account data for the indexed key across different
// slots. As this is rare, it should be ok to use a Vec here over a HashSet, even
// though we are running some key existence checks.
type SecondaryReverseIndexEntry = RwLock<Vec<Pubkey>>;
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

**File:** accounts-db/src/accounts_db.rs (L4915-4925)
```rust
                if !self.account_indexes.is_empty() {
                    // Since StorableAccounts::account() may read the account from disk,
                    // avoid calling it unless secondary indexes are enabled.
                    accounts.account(i, |account| {
                        self.accounts_index.update_secondary_indexes(
                            pubkey,
                            &account,
                            &self.account_indexes,
                        );
                    });
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

**File:** accounts-db/src/accounts_index.rs (L2373-2454)
```rust
        // First write one mint index
        index.upsert(
            slot,
            slot,
            &account_key,
            true,
            &mut ReclaimsSlotList::new(),
            UPSERT_RECLAIM_TEST_DEFAULT,
        );
        index.update_secondary_indexes(
            &account_key,
            &AccountSharedData::create_from_existing_shared_data(
                0,
                Arc::new(account_data1.to_vec()),
                *token_id,
                false,
                0,
            ),
            secondary_indexes,
        );

        // Now write a different mint index for the same account
        index.upsert(
            slot,
            slot,
            &account_key,
            true,
            &mut ReclaimsSlotList::new(),
            UPSERT_RECLAIM_TEST_DEFAULT,
        );
        index.update_secondary_indexes(
            &account_key,
            &AccountSharedData::create_from_existing_shared_data(
                0,
                Arc::new(account_data2.to_vec()),
                *token_id,
                false,
                0,
            ),
            secondary_indexes,
        );

        // Both pubkeys will now be present in the index
        check_secondary_index_mapping_correct(
            secondary_index,
            &[secondary_key1, secondary_key2],
            &account_key,
        );

        // If a later slot also introduces secondary_key1, then it should still exist in the index
        let later_slot = slot + 1;
        index.upsert(
            later_slot,
            later_slot,
            &account_key,
            true,
            &mut ReclaimsSlotList::new(),
            UPSERT_RECLAIM_TEST_DEFAULT,
        );
        index.update_secondary_indexes(
            &account_key,
            &AccountSharedData::create_from_existing_shared_data(
                0,
                Arc::new(account_data1.to_vec()),
                *token_id,
                false,
                0,
            ),
            secondary_indexes,
        );
        assert_eq!(secondary_index.get(&secondary_key1), vec![account_key]);

        // If we set a root at `later_slot`, and clean, then even though the account with secondary_key1
        // was outdated by the update in the later slot, the primary account key is still alive,
        // so both secondary keys will still be kept alive.
        let _ = index.clean_rooted_entries(&account_key, &mut ReclaimsWithNewestSlot::new(), None);

        check_secondary_index_mapping_correct(
            secondary_index,
            &[secondary_key1, secondary_key2],
            &account_key,
        );
```
