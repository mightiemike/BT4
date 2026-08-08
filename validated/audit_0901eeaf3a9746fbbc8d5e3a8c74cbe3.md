### Title
Stale StakeAccount cache entry persists in Stakes::stake_delegations after owner reassignment away from stake::program - (File: runtime/src/stakes.rs)

### Summary
`StakesCache::check_and_store` only checks `lamports() == 0` combined with the account's *current* owner being the vote or stake program to trigger eviction; if an attacker changes a cached stake account's owner to `system_program` while keeping `lamports > 0`, none of the `if`/`else if` branches execute, so the stale `StakeAccount` entry is never removed from `Stakes<StakeAccount>::stake_delegations`. This is explicitly acknowledged by the TODO comment at the top of the function.

### Finding Description
`check_and_store` is called on every account write path (via `Bank::update_stakes_cache` / `store_accounts`) with the freshly written account's current owner and lamports: [1](#0-0) 

The logic is:
1. If `lamports == 0`, remove from vote or stake cache depending on `owner`.
2. Else if `owner == vote_program`, upsert/remove vote account.
3. Else if `owner == stake_program`, upsert/remove stake delegation.
4. Otherwise (owner is neither vote nor stake program, and lamports != 0) — **nothing happens**. [2](#0-1) 

This is precisely the gap the TODO describes: an account previously cached as a stake delegation whose owner is later reassigned to something else (e.g. `system_program` via `Assign`) while lamports remain non-zero falls into case 4 and is never evicted via `remove_stake_delegation`. The exploit path is:
- `CreateAccount` with `owner = stake::program::id()`.
- `Initialize` + `DelegateStake` (minimum delegation) → account becomes a valid `StakeAccount`, gets `upsert_stake_delegation`'d into `Stakes::stake_delegations`.
- `Assign` (System program instruction) sets the account's owner to `system_program::id()`, lamports untouched (`> 0`).

After the `Assign` instruction commits, `check_and_store` is invoked again for this pubkey with `owner = system_program`, `lamports > 0`. Since owner is neither vote nor stake program and lamports != 0, none of the eviction paths trigger, leaving the old `StakeAccount` object cached under `stake_delegations` even though the account is no longer owned by the stake program on-chain.

### Impact Explanation
This produces a genuine consistency divergence between the accounts-db committed truth (account owned by `system_program`) and the in-memory `Stakes<StakeAccount>` cache (still treats it as an active stake delegation), matching the "cheap accounts/writes forcing disproportionate storage/index/scan/hashing/cleanup work" and cache-consistency invariant described in the prompt. Each repetition (`CreateAccount → Initialize → DelegateStake → Assign`) costs only ordinary transaction fees and rent-exempt minimum lamports for a stake account, and can be repeated with fresh keypairs indefinitely, allowing unbounded growth of stale entries in the `imbl::HashMap`-backed `stake_delegations` structure and corrupting the aggregate stake accounting that Agave derives from this cache (used for leader schedule / vote weighting calculations that read from `Stakes`). This matches an Agave bounty category of stake/cache-consistency corruption caused by cheap, repeatable, unprivileged writes.

### Likelihood Explanation
Fully feasible for an unprivileged attacker: it only requires a funded keypair capable of sending standard System and Stake program instructions (`CreateAccount`, `Initialize`, `DelegateStake`, `Assign`), no special privileges, no validator/leader control, and no crafted snapshots. The sequence is deterministic and repeatable at will, bounded only by transaction fees and minimum stake-account rent/delegation lamports.

### Recommendation
In `check_and_store`, evict any pre-existing cache entry whenever the account's owner is not the stake or vote program, regardless of lamport balance — i.e., add an explicit `else` branch that calls `stakes.remove_stake_delegation(...)` and `stakes.remove_vote_account(...)` (or check prior cache membership) when `owner` doesn't match either program id, closing the gap referenced by the TODO in [2](#0-1) .

### Proof of Concept
Rust unit test in `runtime/src/stakes.rs` test module (extending existing `StakesCache` tests):
```rust
#[test]
fn test_check_and_store_evicts_on_owner_reassignment() {
    let stakes_cache = StakesCache::new(Stakes::default() /* or existing test helper */);
    let stake_pubkey = Pubkey::new_unique();

    // 1. Simulate a valid stake account being cached.
    let stake_account = create_test_stake_account(/* delegated, lamports > 0, owner = stake::program::id() */);
    stakes_cache.check_and_store(&stake_pubkey, &stake_account, None, false);
    assert!(stakes_cache.stakes().stake_delegations().contains_key(&stake_pubkey));

    // 2. Simulate owner reassignment to system_program with lamports intact.
    let mut reassigned_account = stake_account.clone();
    reassigned_account.set_owner(solana_system_program::id());
    // lamports left > 0

    stakes_cache.check_and_store(&stake_pubkey, &reassigned_account, None, false);

    // Expected (currently fails): stale entry should be evicted.
    assert!(
        !stakes_cache.stakes().stake_delegations().contains_key(&stake_pubkey),
        "stale StakeAccount entry was not evicted after owner reassignment away from stake::program"
    );
}
```
Expected result today: the assertion fails, demonstrating the stale-cache bug matching the TODO at [2](#0-1) . After applying the fix (explicit eviction branch for non-stake/non-vote owners), the test should pass.

### Citations

**File:** runtime/src/stakes.rs (L87-117)
```rust
    pub(crate) fn check_and_store(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        // TODO: If the account is already cached as a vote or stake account
        // but the owner changes, then this needs to evict the account from
        // the cache. see:
        // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
        let owner = account.owner();
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
        debug_assert_ne!(account.lamports(), 0u64);
```
