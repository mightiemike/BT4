### Title
Unbounded per-pubkey growth of `SecondaryIndex::reverse_index` via repeated account-owner reassignment causes disproportionate insert/remove cost - (File: accounts-db/src/accounts_index/secondary.rs)

### Summary
The `ProgramId` / `SplTokenMint` / `SplTokenOwner` secondary indexes maintain a reverse-index `DashMap<Pubkey, RwLock<Vec<Pubkey>>>` that maps an account (`inner_key`) to every *distinct* secondary-index key it has ever been associated with, not just its current one. Because entries are only purged from this `Vec` when the account is completely removed from the primary accounts index (i.e., fully dead), an unprivileged user can force one account to accumulate a large, unbounded number of stale outer-key mappings simply by repeatedly changing the indexed field (e.g., reassigning the account's owner) across many rooted slots while keeping the account alive. This is directly analogous to the reported `delegate` griefing attack: a cheap, repeatable, unprivileged action grows an internal array that is scanned linearly on every subsequent insert/remove touching that key, disproportionately increasing CPU cost for anyone/anything (clean, flush, future stores) that has to operate on it.

### Finding Description
`SecondaryIndex::insert()` always inserts a new reverse-index mapping for `inner_key -> outer_key`, and the comment at [1](#0-0)  explicitly documents the (violated) assumption:

"The only cases where an inner key should map to a different outer key is if the account had different account data for the indexed key across different slots. As this is rare, it should be ok to use a Vec here over a HashSet, even though we are running some key existence checks."

The insert path does a linear `contains()` scan of this Vec before pushing a new entry: [2](#0-1) . Removal (`remove_by_inner_key_if`) also walks and drains the entire Vec, and for every element performs another DashMap lookup + removal in the forward index: [3](#0-2)  and [4](#0-3) .

Critically, `AccountsDb`'s own comments confirm that stale secondary-index entries are *not* removed when a newer version of the account supersedes an older one — they persist until the pubkey is completely purged from the primary index: [5](#0-4) . This means the primary index's `slot_list` for the pubkey can be compacted down to a single, current entry by ordinary `clean`, while the secondary `reverse_index` Vec for that same pubkey keeps accumulating one entry per distinct historical outer-key value (e.g., every distinct program owner that account has ever had), because "the account dying" and "the account's indexed field changing" are different events.

An unprivileged user can drive this growth cheaply: any account owned by the System Program with zero lamports and zero data can be reassigned to a different owning program via the System Program's `Assign` instruction. Doing this in a new slot each time, then reassigning it back or to yet another owner in the next slot, causes `update_secondary_indexes` (via `AccountsIndex::upsert`) to call `SecondaryIndex::insert()` with a new outer key while the same pubkey (`inner_key`) never fully leaves the primary index. Each iteration:
- Grows the target account's reverse-index `Vec<Pubkey>` by one entry.
- Makes every subsequent `insert()` for that same inner key pay an `O(n)` `contains()` scan under the reverse-index entry's write lock.
- Makes any later `remove_by_inner_key_if()` for that inner key (triggered on flush/clean/purge when the account eventually dies) pay `O(n)` work, draining and individually removing `n` forward-index entries.

This mirrors the reported bug class precisely: a cheap, repeatable, unprivileged operation appends to an array that is walked/searched on essentially every future operation touching that key, and the only bound is the number of times the attacker chooses to act, not any protocol-level cap (there is no `MAX_DELEGATES`-style limit here at all).

### Impact Explanation
This is a griefing / disproportionate-CPU-cost vector confined to validators running with secondary indexes enabled (`--account-index program-id` / `spl-token-mint` / `spl-token-owner`), which is an accounts-index feature, not a network-wide consensus path. It does not corrupt state, cause consensus divergence, or leak funds. Its effect is:
- Slower `insert()`/`update_secondary_indexes` calls for the targeted pubkey (linear in accumulated distinct outer keys) on every write, which adds CPU cost to bank processing for nodes with the index enabled.
- Slower `remove_by_inner_key_if()` when the account eventually dies (purge/clean), competing for the reverse-index entry's lock and doing repeated forward-index DashMap operations.
- Because these operations run under RwLock/DashMap shard locks shared by other pubkeys' index operations, sustained abuse can add measurable, disproportionate overhead to `clean_accounts`/flush paths on affected nodes, i.e. "disproportionate storage and CPU cost" within the accepted impact categories.

This does not rise to a network-wide DoS (Ethereum-gas-limit style hard stop does not exist here, but Solana's per-slot compute budget and secondary indexes being opt-in limit blast radius), so it is best characterized as a griefing/cost-amplification bug rather than a critical vulnerability.

### Likelihood Explanation
Requires:
1. The validator/RPC node to have opted into a secondary index (`--account-index`), which is not the default configuration.
2. The attacker to control an account and repeatedly issue cheap `Assign`-type transactions across multiple slots (ordinary, unprivileged operations, no special permissions needed).

Given index-enabled nodes are typically RPC nodes intentionally exposing `getProgramAccounts`/mint/owner filtering, and the attack requires no elevated access, likelihood is moderate for such nodes and irrelevant for the majority of validators that don't enable secondary indexes.

### Recommendation
- Bound the number of distinct outer-key mappings tracked per inner key in `reverse_index` (analogous to `MAX_DELEGATES`), evicting/coalescing the oldest stale entries once a threshold is exceeded, or
- Proactively purge stale secondary-index entries for a pubkey whenever its indexed field changes (not just when the pubkey fully dies), so `reverse_index` entries stay bounded by the number of *currently* live index associations (which should generally be 1), rather than the full history of associations.
- Alternatively, replace the `Vec` with a `HashSet` (as the removed-comment risk already anticipates) to make `contains()`/removal O(1) amortized, which mitigates but does not fully eliminate the unbounded-growth/memory concern — a bound is still needed to cap worst-case memory and drain cost in `remove_by_inner_key_if`.

### Proof of Concept
Not executed against a live cluster; the following is a conceptual repro based on the code paths cited above (the PoC would need to be validated by an engineer with access to run `agave` test/bench harnesses, which is outside what could be confirmed from static code reading alone):
1. Configure an accounts-db test harness with `AccountSecondaryIndexes` enabling `AccountIndex::ProgramId` (as done in existing unit tests, e.g. `program_id_index_enabled()` used in `accounts-db/src/accounts_db/tests/impl.rs`).
2. Create a zero-lamport, System-Program-owned account.
3. In a loop of N iterations, each in a new rooted slot: reassign the account's owner to a freshly generated program pubkey (simulating repeated `Assign` calls), store the account, and root/flush the slot without letting the pubkey fully die (i.e., keep at least one live version at all times).
4. After N iterations, call `accounts_index.get_index_key_pubkeys`/inspect `SecondaryIndex::reverse_index.get(&inner_key)` and observe its length grows to N, and measure elapsed time of `SecondaryIndex::insert()` calls for this pubkey as N grows (expect roughly linear/superlinear degradation vs. a control pubkey with a single owner), analogous to the 266k-gas-vs-23M-gas comparison in the reported issue.

Because I do not have execution access, the exact quantitative overhead (analogous to the reported "100x") is unconfirmed and would need to be measured with a Devin session or local benchmark against `accounts-db`'s existing secondary-index test infrastructure.

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

**File:** accounts-db/src/accounts_index/secondary.rs (L173-210)
```rust
    /// Removes `inner_key` from `outer_key`'s map.
    ///
    /// Must only be called by remove_by_inner_key_if(), or equiv, that is
    /// holding a lock on self.reverse_index.
    fn remove_index_entries(&self, outer_key: &Pubkey, inner_key: &Pubkey) -> bool {
        let Some(inner_keys) = self.index.get_mut(outer_key) else {
            // we were told that inner_key is in the outer_key map,
            // so the outer_key map should exist!
            panic!(
                "{}: bad index: missing entry for outer_key={outer_key} (inner_key={inner_key})",
                self.metrics_name
            );
        };

        let was_removed = inner_keys.value().remove_inner_key(inner_key);
        if !was_removed {
            // we were told that inner_key is in the outer_key map,
            // so the outer_key map should contain the inner_key!
            panic!(
                "{}: bad index: missing entry for inner_key={inner_key} in map for \
                 outer_key={outer_key}",
                self.metrics_name
            );
        }

        // Before dropping the lock, check if the outer_key map is empty.
        // Because if it is *not* empty, we can skip checking again below.
        let is_outer_key_empty = inner_keys.is_empty();
        drop(inner_keys);

        if is_outer_key_empty {
            // If the outer_key map was empty, we'll check again and remove it if still empty.
            // If it is no longer empty, that is fine, it was re-added, and nothing to do here.
            self.index
                .remove_if(outer_key, |_, inner_keys| inner_keys.is_empty());
        }
        was_removed
    }
```

**File:** accounts-db/src/accounts_index/secondary.rs (L220-250)
```rust
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

**File:** accounts-db/src/accounts_db.rs (L1359-1391)
```rust
    /// Purges each key in `removed_keys` from the enabled secondary indexes, unless the key is
    /// still alive in the write cache. `removed_keys` must be keys that are not present in the
    /// primary index
    ///
    /// The cache check is all-or-nothing per key: a key kept because it is cache-live retains all
    /// of its secondary entries, including stale ones from its dead rooted versions (e.g. an old
    /// mint after the account is re-created with a new one). Scans tolerate stale entries by
    /// post-filtering against account data, and they are removed the next time the key dies while
    /// not cache-resident.
    ///
    /// Cache writes populate the secondary indexes but not the primary index, so a key that is gone
    /// from the primary index can still be alive in the write cache and must keep its secondary
    /// entries. This is tricky due to the races that need to be considered:
    /// 1) Removed from the cache then re-added to the cache by replay
    /// - This is protected by re-checking the cache in the closure passed to purge. Since purge
    ///   holds the secondary index's reverse-index lock when it re-checks cache presence, and a
    ///   cache store writes the cache before inserting into the secondary index under that same
    ///   lock, either the re-check sees the cache write and the entry is not removed, or the
    ///   removal wins and the store's later insert re-adds it.
    /// 2) Removed from the cache, and also simultaneously removed from storage by clean
    /// - Since both the cache removal and the index removal are done before the removal from the
    ///   secondary index, the worst case is a double removal (both paths remove the same secondary
    ///   index entry). This is safe since the secondary index removal is idempotent.
    /// 3) Removed from the storage, but still present in the cache
    /// - This is protected by checking the cache presence in the closure. If the pubkey is still
    ///   present in the cache, the secondary index entry is not removed.
    ///
    /// We do not need to consider removed from cache -> added to storage. Adding to storage
    /// requires a cache entry to be present first, so a fresh store of the key would have to be
    /// rooted and flushed inside this window — impossible because rooting is driven by the same
    /// ReplayStage thread that purges unrooted slots, and clean runs serially with flush on the
    /// ABS thread.
    fn purge_secondary_indexes_for_dead_keys<'a>(
```
