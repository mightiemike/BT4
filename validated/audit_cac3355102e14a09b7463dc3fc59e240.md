### Title
Unvalidated arbitrary "mint" addresses accepted into the SPL-token secondary index enable unbounded in-memory index growth - (File: `accounts-db/src/accounts_index.rs`)

### Summary
The external report's bug class — public functions that accept arbitrary, attacker-supplied token addresses without whitelisting/validation — maps onto Agave's SPL-token secondary indexing path. `update_spl_token_secondary_indexes` accepts a "mint" (or "owner") pubkey that is read verbatim out of account data supplied by any unprivileged user, and inserts it into an in-memory secondary index with no check that the referenced pubkey is an actual, valid mint (or owner) account.

### Finding Description
`AccountsIndex::update_secondary_indexes` (`accounts-db/src/accounts_index.rs:614-660`) is invoked for every stored account and dispatches to `update_spl_token_secondary_indexes` (`accounts-db/src/accounts_index.rs:557-580`): [1](#0-0) 

The only validation performed is:
- `account_owner == token_id` (i.e., the account is owned by the SPL Token / Token-2022 program), and
- the account data is long enough / "initialized" per `GenericTokenAccount::unpack_account_mint`/`unpack_account_owner`.

There is **no verification that the pubkey extracted from the account's mint field actually corresponds to an existing, valid mint account** (or that the "owner" field corresponds to a real wallet). Any unprivileged user can create a token-program-owned account, set the `is_initialized` byte, and write an arbitrary 32 bytes into the mint field (the same "arbitrary address" pattern flagged in the external report for `LockToken.sol`). That arbitrary pubkey is then unconditionally inserted into `spl_token_mint_index` / `spl_token_owner_index`: [2](#0-1) 

The tests confirm the only guard is program-id and data-length, not the validity of the referenced address: [3](#0-2) 

Unlike the primary `AccountsIndex`, which can spill to disk via the bucket-map backed storage (`bucket_map/src/bucket_map.rs`), the SPL-token/program-id secondary indexes are plain in-process `SecondaryIndex` maps (RwLock/DashMap-backed) that are never evicted except when the underlying account is cleaned/purged from the primary index: [4](#0-3) [5](#0-4) 

This secondary indexing is opt-in via the validator's `--account-index` flag (`validator/src/commands/run/args/account_secondary_indexes.rs`), commonly enabled by RPC operators that serve `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByMint` queries.

### Impact Explanation
A node that enables `--account-index spl-token-mint` (or `spl-token-owner`) to serve token-account queries can be forced to grow its in-memory secondary index maps by an amount proportional to the number of distinct (fabricated) mint/owner pubkeys an attacker chooses to write into token-program-owned accounts, none of which need to correspond to real mints or owners. Because insertion requires no validation of the referenced address, an attacker can multiply memory/CPU overhead on the affected node relative to genuine legitimate token activity — a disproportionate storage/CPU cost vector directly analogous to the reported "use of arbitrary token addresses" issue (accepting untrusted addresses without whitelisting them against a known-good set).

### Likelihood Explanation
Likelihood is bounded by two factors: (1) it only affects nodes that explicitly opt into `--account-index`, and (2) each spam account still requires paying normal account-creation rent, so the attack is not free but is a purely storage-based multiplier attack with no economic penalty tied to secondary-index bloat specifically. Any unprivileged user holding minimal SOL for rent-exemption can trigger it repeatedly and indefinitely.

### Recommendation
When updating the SPL-token secondary indexes, verify that the extracted mint/owner pubkey corresponds to a real account with the expected owner/state (e.g., confirm the mint account exists and is actually an initialized mint owned by the same token program) before inserting into `spl_token_mint_index`/`spl_token_owner_index`, or otherwise bound/rate-limit the size of these in-memory secondary indexes per distinct outer key, similar to how `nonReentrant`-style hardening was recommended for the analogous smart-contract report.

### Proof of Concept
1. Enable `--account-index spl-token-mint` on a validator/RPC node.
2. As an unprivileged user, repeatedly create new accounts owned by the SPL Token program, set the "initialized" byte at the appropriate offset, and fill the mint field (bytes `[0, PUBKEY_BYTES)`) with freshly-generated random pubkeys that do not correspond to any real mint account (mirrors the test setup at `accounts-db/src/accounts_db/tests/impl.rs:1943-1974` and `accounts-db/src/accounts_index.rs:2190-2216`, but using arbitrary/never-created mint keys instead of legitimate ones).
3. Observe `AccountsIndex::get_index_key_size(&AccountIndex::SplTokenMint, &mint_key)` growing with an unbounded number of distinct top-level keys in `spl_token_mint_index`, none of which reference a genuine mint, consuming attacker-controlled amounts of validator/RPC-node memory.

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

**File:** accounts-db/src/accounts_index.rs (L646-659)
```rust
        self.update_spl_token_secondary_indexes::<spl_generic_token::token::Account>(
            &spl_generic_token::token::id(),
            pubkey,
            account_owner,
            account_data,
            account_indexes,
        );
        self.update_spl_token_secondary_indexes::<spl_generic_token::token_2022::Account>(
            &spl_generic_token::token_2022::id(),
            pubkey,
            account_owner,
            account_data,
            account_indexes,
        );
```

**File:** accounts-db/src/accounts_index.rs (L2195-2216)
```rust
        // Wrong program id
        index.upsert(
            0,
            0,
            &account_key,
            true,
            &mut ReclaimsSlotList::new(),
            UPSERT_RECLAIM_TEST_DEFAULT,
        );
        index.update_secondary_indexes(
            &account_key,
            &AccountSharedData::create_from_existing_shared_data(
                0,
                Arc::new(account_data.to_vec()),
                Pubkey::default(),
                false,
                0,
            ),
            &secondary_indexes,
        );
        assert!(secondary_index.index.is_empty());
        assert!(secondary_index.reverse_index.is_empty());
```

**File:** accounts-db/src/accounts_index/secondary.rs (L43-61)
```rust
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum AccountIndex {
    ProgramId,
    SplTokenMint,
    SplTokenOwner,
}

#[derive(Debug, Clone, Copy)]
pub enum IndexKey {
    ProgramId(Pubkey),
    SplTokenMint(Pubkey),
    SplTokenOwner(Pubkey),
}

// The only cases where an inner key should map to a different outer key is
// if the key had different account data for the indexed key across different
// slots. As this is rare, it should be ok to use a Vec here over a HashSet, even
// though we are running some key existence checks.
type SecondaryReverseIndexEntry = RwLock<Vec<Pubkey>>;
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L2077-2130)
```rust
// Verify that purge_keys_exact does not remove pubkeys from the secondary index if the pubkey
// is still present in the write cache
#[test]
fn test_clean_retains_secondary_index_for_still_cached_key() {
    let accounts = AccountsDb {
        account_indexes: spl_token_mint_index_enabled(),
        ..AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG)
    };
    let pubkey = solana_pubkey::new_rand();
    let index_slot = 1;
    let cache_slot = 2;

    // Set up a token account to be added to the secondary index.
    const SPL_TOKEN_INITIALIZED_OFFSET: usize = 108;
    let mint_key = Pubkey::new_unique();
    let mut account_data_with_mint = vec![0; spl_generic_token::token::Account::get_packed_len()];
    account_data_with_mint[..PUBKEY_BYTES].clone_from_slice(&(mint_key.to_bytes()));
    account_data_with_mint[SPL_TOKEN_INITIALIZED_OFFSET] = 1;
    let mut token_account = AccountSharedData::new(1, 0, &spl_generic_token::token::id());
    token_account.set_data(account_data_with_mint);

    let zero_account = AccountSharedData::new(0, 0, &Pubkey::default());

    // Slot 0: a rooted non-zero version so the tombstone below reaches storage and the index
    store_rooted_nonzero_accounts(&accounts, 0, [&pubkey]);

    // Slot 1: a rooted zero-lamport tombstone. Store with `PubkeysToStore::All` so it is not
    // reclaimed
    accounts.store_for_tests((index_slot, [(&pubkey, &zero_account)].as_slice()));
    accounts.add_root(index_slot);
    accounts.flush_accounts_cache_slot_for_tests(index_slot);

    // Slot 2: the account is written to the write cache,
    accounts.store_for_tests((cache_slot, [(&pubkey, &token_account)].as_slice()));
    assert_eq!(
        accounts
            .accounts_index
            .get_index_key_size(&AccountIndex::SplTokenMint, &mint_key),
        Some(1),
    );

    // Clean removes the entry from the accounts index (as the newest rooted version is zero
    // lamport)
    accounts.clean_accounts_for_tests();

    // The pubkey is still live in the write cache, so its secondary index entry must survive.
    assert!(accounts.accounts_cache.contains_pubkey(&pubkey));
    assert_eq!(
        accounts
            .accounts_index
            .get_index_key_size(&AccountIndex::SplTokenMint, &mint_key),
        Some(1),
        "clean purged the secondary index entry for a live cached account",
    );
```
