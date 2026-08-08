### Title
Failed mint-parse results are never cached, causing repeated redundant `bank.get_account` + `StateWithExtensions::<Mint>::unpack` work per malformed mint within a single RPC call - ([File: rpc/src/parsed_token_accounts.rs])

### Finding Description
`get_parsed_token_accounts` builds a per-call `mint_data: HashMap<Pubkey, AccountAdditionalDataV3>` cache to avoid re-fetching/re-parsing the same mint account for every token account that references it [1](#0-0) . For each keyed token account, it extracts the mint pubkey via `get_token_account_mint`, then does `mint_data.get(&mint_pubkey).cloned().or_else(...)`. Inside the fallback closure it calls `get_mint_owner_and_additional_data(&bank, &mint_pubkey).ok()?` — if this returns `Err`, the `?` operator short-circuits and returns `None` from the closure *before* reaching the `mint_data.insert(...)` line [2](#0-1) .

`get_mint_owner_and_additional_data` in turn calls `bank.get_account(mint)` and then `get_additional_mint_data(bank, mint_account.data())`, which calls `StateWithExtensions::<Mint>::unpack(data)` and maps any unpack failure to `Err(Error::invalid_params(...))` [3](#0-2) .

Consequently, only *successful* mint parses get cached. A mint account whose bytes are constructed to always fail `StateWithExtensions::<Mint>::unpack` (e.g., truncated/malformed TLV extension bytes) will cause the closure to return `None` every time, so `mint_data.insert` is never executed and the cache entry for that mint pubkey is never populated. Every subsequent token account in the same iterator that references this same corrupted mint re-triggers a fresh `bank.get_account(mint)` lookup and a fresh `StateWithExtensions::<Mint>::unpack` attempt over the full mint account data, instead of reusing a cached (or cached-failure) result.

An attacker can:
1. Create an account owned by `spl_token_interface::id()` (or the token-2022 program) sized as a mint with a corrupted TLV extension tail so `StateWithExtensions::<Mint>::unpack` always errors.
2. Create N token accounts (owned by the same token program) whose `mint` field points at this malformed mint.
3. Issue a single `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`/`getProgramAccounts` (with `jsonParsed` encoding) call that returns all N accounts — this flows through `get_parsed_token_accounts` [4](#0-3) [5](#0-4) [6](#0-5) .

Because the malformed mint's data is attacker-controlled (up to the maximum permitted account size), and the cache never absorbs the failure, the mint account's bytes are re-fetched and re-scanned by the TLV unpack loop N times instead of once within that single call, multiplying cost by the number of token accounts sharing the bad mint.

### Impact Explanation
This is a CPU/work-amplification bug scoped to a single RPC call: the cost of resolving additional mint data for a corrupted mint scales as O(N × size_of_mint_account) instead of O(size_of_mint_account) + O(N), because the negative (`Err`) result is never memoized. This falls under the "disproportionate CPU cost" bounty category for RPC-serving code, and is triggerable by an unprivileged user who only creates/writes accounts they own and issues one filtered RPC call (`getTokenAccountsByOwner` uses the secondary token-owner index, so it is not the excluded "unfiltered getProgramAccounts" case). No consensus, hash, snapshot, or capitalization impact — the effect is confined to `Result` values computed transiently for RPC JSON encoding; the account/bank state itself is unaffected.

### Likelihood Explanation
Feasible and repeatable with only standard, unprivileged account creation: any user can create an account under a `spl_token_interface`/token-2022 program with arbitrary bytes and any number of token accounts pointing at it, then issue one `getTokenAccountsByOwner` call. No special privileges, timing, or multiple clients are required, and it reproduces deterministically every time the same malformed mint is referenced by multiple accounts in one query.

### Recommendation
Cache negative results as well as positive ones (e.g., store an `Option<AccountAdditionalDataV3>` or a distinguishing "failed" sentinel in `mint_data`, or use `HashMap::entry(...).or_insert_with(...)` so the closure is invoked at most once per mint pubkey per call regardless of success or failure). This ensures `get_mint_owner_and_additional_data` is called at most once per distinct mint per RPC call.

### Proof of Concept
Rust integration test outline (add near existing tests in `rpc/src/rpc.rs`, e.g. alongside `test_token_account_...` tests around line 8401+):
```rust
#[test]
fn test_get_token_accounts_by_owner_malformed_mint_reparse_cost() {
    // 1. Build a bank and RPC handler as in existing get_token_accounts_by_owner tests.
    // 2. Create a "mint" account owned by spl_token_interface::id() whose data length
    //    equals a valid extended-Mint size but whose extension TLV bytes are corrupted
    //    (e.g., a length field that overruns the buffer) so that
    //    StateWithExtensions::<Mint>::unpack always returns Err.
    // 3. Store this fake mint via bank.store_account(&fake_mint_pubkey, &fake_mint_account).
    // 4. Create N (e.g. 500) valid-looking TokenAccount entries, all with `mint: fake_mint_pubkey`
    //    and `owner: owner_pubkey`, and store them via bank.store_account.
    // 5. Instrument (or wrap) bank.get_account / StateWithExtensions::<Mint>::unpack with a
    //    call counter (e.g. via a test-only wrapper or by measuring wall-clock time scaling
    //    with N) to assert the number of unpack attempts.
    // 6. Call rpc.get_token_accounts_by_owner(owner, TokenAccountsFilter::ProgramId(program_id),
    //    Some(RpcAccountInfoConfig { encoding: Some(UiAccountEncoding::JsonParsed), .. }), false).
    // 7. Assert: current buggy behavior invokes get_mint_owner_and_additional_data
    //    (and therefore bank.get_account + StateWithExtensions::<Mint>::unpack) N times for the
    //    same fake_mint_pubkey, rather than once, demonstrating cache_hits == 0 despite N lookups
    //    of an identical key.
}
```
Expected assertion after fix: number of `get_mint_owner_and_additional_data` invocations for a given mint pubkey within one `get_parsed_token_accounts` call is at most 1, regardless of success or failure, i.e. work is proportional to the number of *distinct* mints, not the number of token accounts referencing a failing mint.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L59-70)
```rust
    let mut mint_data: HashMap<Pubkey, AccountAdditionalDataV3> = HashMap::new();
    keyed_accounts.filter_map(move |(pubkey, account)| {
        let additional_data = get_token_account_mint(account.data()).and_then(|mint_pubkey| {
            mint_data.get(&mint_pubkey).cloned().or_else(|| {
                let (_, data) = get_mint_owner_and_additional_data(&bank, &mint_pubkey).ok()?;
                let data = AccountAdditionalDataV3 {
                    spl_token_additional_data: Some(data),
                };
                mint_data.insert(mint_pubkey, data);
                Some(data)
            })
        });
```

**File:** rpc/src/parsed_token_accounts.rs (L92-114)
```rust
pub(crate) fn get_mint_owner_and_additional_data(
    bank: &Bank,
    mint: &Pubkey,
) -> Result<(Pubkey, SplTokenAdditionalDataV2)> {
    if mint == &spl_token_interface::native_mint::id() {
        Ok((
            spl_token_interface::id(),
            SplTokenAdditionalDataV2::with_decimals(spl_token_interface::native_mint::DECIMALS),
        ))
    } else {
        let mint_account = bank.get_account(mint).ok_or_else(|| {
            Error::invalid_params("Invalid param: could not find mint".to_string())
        })?;
        let mint_data = get_additional_mint_data(bank, mint_account.data())?;
        Ok((*mint_account.owner(), mint_data))
    }
}

fn get_additional_mint_data(bank: &Bank, data: &[u8]) -> Result<SplTokenAdditionalDataV2> {
    StateWithExtensions::<Mint>::unpack(data)
        .map_err(|_| {
            Error::invalid_params("Invalid param: Token mint could not be unpacked".to_string())
        })
```

**File:** rpc/src/rpc.rs (L652-655)
```rust
        let accounts = if is_known_spl_token_id(&program_id)
            && encoding == UiAccountEncoding::JsonParsed
        {
            get_parsed_token_accounts(Arc::clone(&bank), keyed_accounts.into_iter()).collect()
```

**File:** rpc/src/rpc.rs (L2170-2171)
```rust
        let accounts = if encoding == UiAccountEncoding::JsonParsed {
            get_parsed_token_accounts(bank.clone(), keyed_accounts.into_iter()).collect()
```

**File:** rpc/src/rpc.rs (L2236-2237)
```rust
        let accounts = if encoding == UiAccountEncoding::JsonParsed {
            get_parsed_token_accounts(bank.clone(), keyed_accounts.into_iter()).collect()
```
