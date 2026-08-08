## Title
Positional dedup in `enqueue_on_chain_accounts_lt_hash_updates` silently drops lt-hash updates when a duplicate pubkey appears in `accounts_to_store` out of commit order - (File: `runtime/src/bank/accounts_lt_hash.rs`)

### Summary
This is analogous to the Blueberry `CurveTricryptoOracle` bug: the oracle assumed a *fixed position* (`tokens[2]`) always corresponds to "the WETH leg" without verifying it, so whenever the real ordering differed, the wrong value was priced in. In `Bank::enqueue_on_chain_accounts_lt_hash_updates`, the dedup logic assumes that **iterating `accounts` in reverse index order is equivalent to iterating in "most-recently-committed" order** for a given pubkey, and uses that positional assumption — rather than an explicit recency check — to decide which version of an account to mix into the lattice hash. If the `StorableAccounts` collection ever contains the same pubkey more than once in an order where a later array index is not actually the most recent write (e.g., a duplicate/re-touched account inserted earlier in commit order but later in the vector, or any future caller of this function that does not maintain "vector index == commit order"), the function will silently hash the wrong version of the account.

### Finding Description
`Bank::enqueue_on_chain_accounts_lt_hash_updates` is documented as deduplicating so that only "the latest version of each account" is mixed into the accounts lattice hash: [1](#0-0) 

The dedup mechanism is purely positional: it walks `accounts` from the last index to the first (`for index in (0..accounts.len()).rev()`), and the *first* time it sees a pubkey (i.e., at the highest index touched) it treats that as authoritative and skips every subsequent (lower-index) occurrence of the same pubkey via a `seen_accounts` `HashSet`. There is no check against an actual write-version, slot, or transaction ordering — the code trusts that "higher index in `accounts`" == "most recent write," exactly the same class of trust the oracle placed in "last token slot" == "WETH."

This function is invoked from `Bank::commit_transactions` with `accounts_to_store`, built by `collect_accounts_to_store` from a batch of transactions: [2](#0-1) 

`collect_accounts_to_store`'s contract ("Callers must ensure there are no duplicates") is enforced today, and today's caller happens to preserve index-order-equals-commit-order. But `enqueue_on_chain_accounts_lt_hash_updates` takes a generic `impl StorableAccounts<'a>` and has no internal defense: it neither asserts uniqueness of pubkeys/positions nor verifies that a higher array index actually corresponds to a later write. Any future caller (or a latent bug in `collect_accounts_to_store`/batch construction) that supplies the same pubkey twice with the newer write at a *lower* index than the older write would cause this function to mix in the *stale* account state and mix out the wrong previous state, exactly as the oracle mixed in the wrong "ethPrice" when WETH wasn't actually at `tokens[2]`.

Contrast this with the equivalent and correctly-defensive mechanism already used elsewhere in the accounts index — `AccountsIndex::latest_slot()` — which explicitly compares `Slot` values (not vector position) to determine which slot list entry is the most recent: [3](#0-2) 

That function makes no positional assumption; it derives "latest" from the actual `Slot` number. `enqueue_on_chain_accounts_lt_hash_updates`, by contrast, derives "latest" purely from array position within `accounts`, which is a strictly weaker and unverified invariant — the same category of unverified positional assumption that caused the oracle bug.

### Impact Explanation
If the positional assumption is ever violated (e.g., a future refactor of `collect_accounts_to_store`, a batch-processing path that reorders accounts before calling this function, or a duplicate pubkey introduced by retryable/duplicate transactions in the same batch where highest array index is not actually most-recent), the accounts lattice hash update computed here would mix in the wrong version of an account (mixing out the wrong "previous" state and mixing in an out-of-date "current" state). Because `accounts_lt_hash` feeds directly into the bank hash (via `finish_accounts_lt_hash_updates` → `hash_internal_state`) and is verified against the independently-recomputed startup value in `Bank::verify_accounts`, this class of bug produces a **hash/capitalization divergence**: an honest node computing the lt-hash incrementally (this path) would disagree with a node recomputing it from the index at startup, exactly the "honest-node snapshot-vs-replay mismatch" category called out as acceptable impact.

### Likelihood Explanation
Today's single caller (`commit_transactions` → `collect_accounts_to_store`) appears to preserve index order equal to commit order, so the bug is **not currently triggered** in that call site as far as could be verified from the code read. This assessment is best characterized as **latent/defense-in-depth**: the function itself contains no assertion or slot/version check to guarantee its documented invariant ("process accounts in reverse because we must only count the latest version of each account") actually holds; it merely comments that assumption. I was not able to fully trace every code path that constructs the `StorableAccounts` passed into this function to rule out a duplicate-pubkey-out-of-order scenario, so likelihood should be treated as uncertain/low without further investigation of `collect_accounts_to_store` and any other in-repo callers of `enqueue_on_chain_accounts_lt_hash_updates`.

### Recommendation
Do not rely on array position alone to determine "latest version of the account." Either (a) have `StorableAccounts` implementations that could contain duplicate pubkeys expose an explicit write-order/version per index and have `enqueue_on_chain_accounts_lt_hash_updates` compare that value (mirroring `AccountsIndex::latest_slot`'s explicit `Slot` comparison rather than trusting position), or (b) add a debug-assertion that no pubkey appears more than once in `accounts` for callers where reverse-array-order is assumed equivalent to commit order, so any future caller violating the invariant fails loudly instead of silently corrupting the lattice hash.

### Proof of Concept
Not applicable as a runnable exploit — under the current single call site (`Bank::commit_transactions`), the invariant "array index order == commit order" holds, so no concrete divergence was reproduced. The finding is a structural/defense-in-depth gap: `enqueue_on_chain_accounts_lt_hash_updates` has no internal verification of its own documented invariant, analogous to the oracle trusting "last position == WETH" without checking token addresses. Confirming exploitability would require auditing every present and future caller of this function (and of `collect_accounts_to_store`) for cases where a pubkey could appear more than once with the newer write at a lower array index — this was not fully verifiable within the available tooling/index.

### Citations

**File:** runtime/src/bank/accounts_lt_hash.rs (L30-57)
```rust
    /// Enqueues the accounts lt hash updates for `accounts` to the accounts hasher thread pool.
    ///
    /// This fn is meant to be called by on-chain events, e.g. transaction processing.
    /// This fn deduplicates from `accounts`, keeping only the latest version of each account.
    /// It also loads the previous version of each account inline, because we assume the previous
    /// version of each account is still in the accounts write cache, and thus fast to load.
    ///
    /// For non-transaction processing callers, consider `enqueue_off_chain_accounts_lt_hash_updates()`.
    pub fn enqueue_on_chain_accounts_lt_hash_updates<'a>(
        &self,
        accounts: &impl StorableAccounts<'a>,
    ) {
        if accounts.is_empty() {
            return;
        }

        let seen_accounts_freelist = seen_accounts_freelist();
        let mut seen_accounts = seen_accounts_freelist.try_pop().unwrap_or_default();
        let async_progress = &self.accounts_lt_hash_async_progress;
        let thread_pool = accounts_hasher_thread_pool();

        // process accounts in reverse because we must only count the latest version of each account
        for index in (0..accounts.len()).rev() {
            let address = accounts.pubkey(index);
            if !seen_accounts.insert(*address) {
                // we've already enqueued a newer update for the same account; skip this one
                continue;
            }
```

**File:** runtime/src/bank.rs (L4370-4378)
```rust
            let (accounts_to_store, transactions) = collect_accounts_to_store(
                sanitized_txs,
                &maybe_transaction_refs,
                &processing_results,
            );

            let to_store = (self.slot(), accounts_to_store.as_slice());
            self.update_bank_hash_stats(&to_store);
            self.enqueue_on_chain_accounts_lt_hash_updates(&to_store);
```

**File:** accounts-db/src/accounts_index.rs (L429-465)
```rust
    // Given a SlotList `L`, a list of ancestors and a maximum slot, find the latest element
    // in `L`, where the slot `S` is an ancestor or root, and if `S` is a root, then `S <= max_root`
    pub(crate) fn latest_slot(
        &self,
        ancestors: Option<&Ancestors>,
        slot_list: &[SlotListItem<T>],
        max_root_inclusive: Option<Slot>,
    ) -> Option<usize> {
        let mut current_max = 0;
        let mut rv = None;
        if let Some(ancestors) = ancestors
            && !ancestors.is_empty()
        {
            for (i, (slot, _t)) in slot_list.iter().rev().enumerate() {
                if (rv.is_none() || *slot > current_max) && ancestors.contains_key(slot) {
                    rv = Some(i);
                    current_max = *slot;
                }
            }
        }

        // If we found an ancestor, then we can return early without checking the roots
        // If there is a root that is newer than the newest ancestor but not an ancestor
        // then the root is from a different fork and should not be returned
        if let Some(rv) = rv {
            return Some(slot_list.len() - 1 - rv);
        }

        let max_root_inclusive = max_root_inclusive.unwrap_or(Slot::MAX);

        slot_list
            .iter()
            .enumerate()
            .filter(|(_, (slot, _t))| *slot <= max_root_inclusive)
            .max_by_key(|(_, (slot, _t))| *slot)
            .map(|(index, _)| index)
    }
```
