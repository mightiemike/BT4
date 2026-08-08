### Title
Unbounded, per-write growth of `SecondaryIndex` forward/reverse-index Vec entries for a single live account - ([File: accounts-db/src/accounts_index/secondary.rs])

### Summary
`SecondaryIndex::insert` appends a new outer-key (or forward-index) entry every time the *decoded* mint/owner bytes of an account change, and that history is only ever pruned by `remove_by_inner_key_if`, which fires solely when the account's index entry is fully removed (i.e., the account dies). A user who repeatedly rewrites one live, funded account with a distinct SPL-token mint/owner value each slot causes `reverse_index`'s `Vec<Pubkey>` for that account, and the number of top-level `index` entries referencing it, to grow linearly and permanently for as long as the account stays alive, at O(n) cost per insert due to the linear `contains()` scan.

### Finding Description
`SecondaryIndex::insert(key, inner_key)` unconditionally pushes a new outer key into the account's reverse-index `Vec<Pubkey>` whenever it isn't already present: [1](#0-0) 

The type comment explicitly acknowledges — and dismisses — this growth path as an accepted tradeoff, assuming key changes are rare: [2](#0-1) 

The only cleanup path, `remove_by_inner_key_if`, drains the reverse-index Vec and removes the corresponding forward-index entries, but it is documented and designed to run only when the caller decides the whole index entry should be dropped (i.e., the account has no live/refcounted index entry left, such as when it dies or is fully purged): [3](#0-2) 

Because there is no per-slot or per-write reconciliation that removes the *previous* mint/owner mapping when an account's indexed field changes while the account remains alive, each distinct encoded value the attacker writes:
1. Adds one more entry to the account's `reverse_index` `Vec<Pubkey>` (line 151-153), which is scanned with `contains()` on every subsequent insert, making insert cost linear in the number of distinct historical values (O(n) per write, O(n²) total for n writes).
2. Adds one more permanent top-level entry to `self.index` (the forward map) keyed by the new mint/owner value, each holding a `HashSet` containing this account's pubkey, which is never cleaned up while the account is alive.

None of the account slot/ancestor, zero-lamport, or refcount guards in `accounts_index.rs`/`accounts_db.rs` intervene here, because those guard when an index *entry* is removed, not when an already-alive account's *indexed field value* changes across writes. The attacker needs no elevated privilege — only the ability to own a program (or use an SPL-token-compatible layout) and repeatedly write to their own account with a distinct 32-byte mint/owner field, one write per slot, at normal transaction-fee cost.

### Impact Explanation
This is a resource-cost proportionality violation / memory-and-CPU exhaustion vector: a single funded account controlled by one unprivileged attacker can force permanent, unbounded heap growth in the validator's `DashMap<Pubkey, RwLock<Vec<Pubkey>>>` reverse index and `DashMap<Pubkey, SecondaryIndexEntryType>` forward index, plus growing (eventually quadratic) CPU cost per write, for a cost proportional only to per-transaction fees rather than to the resulting persistent memory footprint. This falls under Agave's resource-exhaustion / disproportionate storage-and-CPU-cost bounty category, scoped to nodes running with secondary indexes enabled (`--account-index spl-token-mint`/`spl-token-owner`).

### Likelihood Explanation
Preconditions are modest but non-default: the validator/RPC node must be run with secondary indexes enabled (`AccountSecondaryIndexes`, opt-in via `--account-index`), and the attacker needs one live, funded account that is never closed. Given that, the attack is trivially repeatable — one transaction per slot rewriting the account's mint/owner-shaped bytes to a fresh 32-byte value — with no special permissions, and the growth is deterministic and monotonic for as long as the account is kept alive.

### Recommendation
Track and remove the account's *previous* indexed value when its indexed field changes for a still-alive account (e.g., diff the newly decoded mint/owner against the last-known value on write and call an analogous "replace" path that both pushes the new outer key and removes the stale one from `reverse_index`/`index`), rather than relying solely on full-account-death cleanup via `remove_by_inner_key_if`. Additionally, consider bounding/deduplicating `reverse_index` entries (e.g., replacing the linear-scan `Vec` with a bounded structure) and/or capping metrics-visible growth so it can be alerted on.

### Proof of Concept
```rust
// accounts-db/src/accounts_index/secondary.rs (test module)
#[test]
fn test_reverse_index_unbounded_growth_for_live_account() {
    let secondary_index = SecondaryIndex::<RwLockSecondaryIndexEntry>::new("test");
    let account_pubkey = Pubkey::new_unique(); // the single "outer_key" account, never removed

    const K: usize = 10_000;
    for _ in 0..K {
        let mint = Pubkey::new_unique(); // simulate rewriting the account's mint field each slot
        secondary_index.insert(&mint, &account_pubkey);
    }

    // Reverse-index entry for the account keeps growing without bound / without reclamation,
    // since remove_by_inner_key_if is never invoked (account never "dies").
    let reverse_entry = secondary_index.reverse_index.get(&account_pubkey).unwrap();
    assert_eq!(reverse_entry.read().unwrap().len(), K);

    // Forward index also accumulates one permanent entry per distinct mint value.
    assert_eq!(secondary_index.index.len(), K);
}
```
Expected result: both assertions pass, demonstrating that `len()` grows linearly in `K` with no reclamation while `account_pubkey` remains "alive" (i.e., `remove_by_inner_key_if` is never called), confirming unbounded, fee-disproportionate memory growth from a single account.

### Citations

**File:** accounts-db/src/accounts_index/secondary.rs (L57-61)
```rust
// The only cases where an inner key should map to a different outer key is
// if the key had different account data for the indexed key across different
// slots. As this is rare, it should be ok to use a Vec here over a HashSet, even
// though we are running some key existence checks.
type SecondaryReverseIndexEntry = RwLock<Vec<Pubkey>>;
```

**File:** accounts-db/src/accounts_index/secondary.rs (L133-153)
```rust
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

**File:** accounts-db/src/accounts_index/secondary.rs (L212-250)
```rust
    /// Removes `inner_key` from the secondary index, if the closure `should_remove` returns true.
    ///
    /// `should_remove` is evaluated while holding `inner_key`'s reverse-index entry lock. Because
    /// `insert()` acquires that same lock before adding a mapping, holding it across the check
    /// serializes this removal against a concurrent `insert(_, inner_key)`. This only yields a
    /// correct decision if writers update the state that `should_remove` reads before calling
    /// `insert()`; otherwise the check can pass against stale state and remove a mapping that a
    /// concurrent writer expects to survive.
    pub fn remove_by_inner_key_if(&self, inner_key: &Pubkey, should_remove: impl Fn() -> bool) {
        // Note: Always lock the reverse-index first, so we synchronize with insert().
        let DashMapEntry::Occupied(reverse_index_entry) = self.reverse_index.entry(*inner_key)
        else {
            // if inner_key doesn't exist in the reverse-index, nothing to do here
            return;
        };

        // Re-check under the reverse-index entry lock. If the caller no longer wants the key
        // removed (e.g. it was concurrently re-added), leave its mapping in place.
        if !should_remove() {
            return;
        }

        // First go through the reverse-index and remove inner_key from all forward-indexes.
        let num_removed = reverse_index_entry
            .get()
            .write()
            .unwrap()
            .drain(..)
            .map(|outer_key| self.remove_index_entries(&outer_key, inner_key) as u64)
            .sum();

        // And now after removing inner_key from all forward-indexes,
        // remove its entry from the reverse-index.
        reverse_index_entry.remove();

        self.stats
            .num_inner_keys
            .fetch_sub(num_removed, Ordering::Relaxed);
    }
```
