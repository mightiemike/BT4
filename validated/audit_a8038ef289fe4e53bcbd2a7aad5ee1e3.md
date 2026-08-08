### Title
Aggregate "total-space" shrink ratio can be diluted by low-cost near-full candidate storages, stalling shrink of genuinely sparse storages - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::select_candidates_by_total_usage` decides whether to shrink each shrink-candidate storage using a single combined (`total_alive_bytes` / `total_bytes`) ratio computed across *all* candidate storages, rather than evaluating each storage independently. This mirrors the Smilee bonding-curve bug class: a shared aggregate metric derived from heterogeneous, individually-controllable components governs an economically/operationally significant decision for all of them together, letting an actor cheaply bias the aggregate to affect the outcome for unrelated components.

### Finding Description
`is_candidate_for_shrink()` under the default `AccountShrinkThreshold::TotalSpace` mode admits a storage into the shrink-candidate pool as soon as it has *any* dead byte (`alive_bytes < total_bytes`): [1](#0-0) 

`select_candidates_by_total_usage` then sorts all current candidates by their individual alive ratio (sparsest first) and walks the list, but the stopping condition is computed from `total_alive_bytes`/`total_bytes` summed over the *entire* candidate set up front: [2](#0-1) 

Crucially, once the aggregate ratio exceeds `shrink_ratio` (default `0.80`) the totals are never updated again in that branch. Sparse (legitimately shrink-worthy, low alive-ratio) storages encountered afterward are pushed into `shrink_slots_next_batch` (deferred, not shrunk this round), and once a storage with its *own* alive_ratio ≥ `shrink_ratio` is reached, the loop `break`s entirely — silently dropping all remaining (denser) candidates from consideration for the round.

Because admission into the candidate pool only requires one dead byte, an unprivileged user can cheaply manufacture many storages that are "barely" shrink candidates (alive_ratio ≈ 0.999, e.g. a large slot of alive accounts plus one small closed account) and keep them present in `shrink_candidate_slots`. These dilute the aggregate `total_alive_bytes/total_bytes` toward 1.0. Any genuinely sparse storage (e.g., another user's slot with heavy churn, alive_ratio well below 0.8) computed in the same batch will then see the pre-inflated aggregate ratio already `> shrink_ratio` on the very first loop iteration, causing it to be deferred to `next_batch` instead of shrunk, and any near-full padding storages sorted after it can trigger the `break`, dropping the rest of the round entirely.

This is structurally identical to the reported bug class: a pooled/aggregate metric computed over asymmetric, attacker-influenceable components gates a decision (bonding-curve pricing there; shrink eligibility here), and cheap manipulation of one component (many near-full storages) distorts the outcome for the others (genuinely sparse storages) that share the same aggregate.

### Impact Explanation
Repeated, low-cost creation of "barely qualifying" candidate storages lets an unprivileged user stall shrinking of unrelated, genuinely sparse account storages across shrink rounds. Since shrink is the mechanism that reclaims disk space from dead/overwritten account bytes, indefinitely deferring it causes disk usage (and the I/O/CPU cost of scanning ever-larger append-vec storages during clean/shrink/snapshot generation) to grow disproportionately relative to genuine chain state growth — a disproportionate storage/CPU cost impact on the validator, achievable by any account-creating client.

### Likelihood Explanation
The `is_candidate_for_shrink` bar for `TotalSpace` mode is extremely low (any single dead byte), so manufacturing many "barely qualifying" candidate storages is inexpensive and requires no special privileges — only ordinary account creation/closure transactions submitted across enough slots to populate `shrink_candidate_slots` with attacker-controlled entries. The effect compounds every time `shrink_candidate_slots` runs (each epoch/round), as an attacker can keep replenishing dilutive candidates.

### Recommendation
Avoid using a single up-front aggregate ratio computed from the full candidate set to gate every storage's eligibility. Options: (1) recompute the running aggregate ratio incrementally as storages are actually selected for shrink (excluding not-yet-decided storages from the denominator), (2) require the aggregate check to only consider storages processed so far rather than the full set’s totals, or (3) cap the influence any single "barely qualifying" storage (alive_ratio very close to 1.0) can have on the aggregate, ensuring genuinely sparse storages are evaluated independently of how many near-full storages happen to be present in the same batch.

### Proof of Concept
Using the existing test harness in `accounts_db/tests/impl.rs` (`test_select_candidates_by_total_usage_2_way_split_condition`, `test_select_candidates_by_total_usage_all_clean`) as a template: [3](#0-2) 
1. Create N "victim" storages with a genuinely low alive_ratio (e.g. `num_alive_bytes` set to 10% of `written_bytes`).
2. Create M "padding" storages with `num_alive_bytes` set to `written_bytes - 1` (i.e., alive_ratio ≈ 0.9999), where M is large enough that the combined `total_alive_bytes/total_bytes` across all N+M candidates exceeds `DEFAULT_ACCOUNTS_SHRINK_RATIO` (0.80).
3. Insert all N+M into `ShrinkCandidates` and call `select_candidates_by_total_usage`.
4. Observe that the victim (genuinely sparse) storages are returned only in `shrink_slots_next_batch` (deferred) rather than `shrink_slots` (selected), and that repeating this with fresh padding storages each round can perpetually defer the victims' shrink.

### Citations

**File:** accounts-db/src/accounts_db.rs (L2992-3071)
```rust
    fn select_candidates_by_total_usage(
        &self,
        shrink_slots: &ShrinkCandidates,
        shrink_ratio: f64,
    ) -> (IntMap<Slot, Arc<AccountStorageEntry>>, ShrinkCandidates) {
        struct StoreUsageInfo {
            slot: Slot,
            alive_ratio: f64,
            alive_bytes_after_shrink: u64,
            store: Arc<AccountStorageEntry>,
        }
        let mut store_usages = Vec::with_capacity(shrink_slots.len());
        let mut total_alive_bytes: u64 = 0;
        let mut total_bytes: u64 = 0;
        for slot in shrink_slots {
            let Some(store) = self.storage.get_slot_storage_entry(*slot) else {
                continue;
            };
            let alive_bytes_after_shrink = self.alive_bytes_after_shrink(&store) as u64;
            total_alive_bytes += alive_bytes_after_shrink;
            let written_bytes = store.written_bytes();
            total_bytes += written_bytes;
            debug_assert!(
                written_bytes > 0,
                "shrink candidate has zero written bytes! slot: {slot} id: {}",
                store.id(),
            );
            let alive_ratio = alive_bytes_after_shrink as f64 / written_bytes as f64;
            store_usages.push(StoreUsageInfo {
                slot: *slot,
                alive_ratio,
                alive_bytes_after_shrink,
                store: store.clone(),
            });
        }
        store_usages.sort_by(|a, b| {
            a.alive_ratio
                .partial_cmp(&b.alive_ratio)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        // Working from the beginning of store_usage which are the most sparse and see when we can stop
        // shrinking while still achieving the overall goals.
        let mut shrink_slots = IntMap::default();
        let mut shrink_slots_next_batch = ShrinkCandidates::default();
        for store_usage in &store_usages {
            let store = &store_usage.store;
            let alive_ratio = (total_alive_bytes as f64) / (total_bytes as f64);
            debug!(
                "alive_ratio: {:?} store_id: {:?}, store_ratio: {:?} requirement: {:?}, \
                 total_bytes: {:?} total_alive_bytes: {:?}",
                alive_ratio,
                store_usage.store.id(),
                store_usage.alive_ratio,
                shrink_ratio,
                total_bytes,
                total_alive_bytes
            );
            if alive_ratio > shrink_ratio {
                // we have reached our goal, stop
                debug!(
                    "Shrinking goal can be achieved at slot {:?}, total_alive_bytes: {:?} \
                     total_bytes: {:?}, alive_ratio: {:}, shrink_ratio: {:?}",
                    store_usage.slot, total_alive_bytes, total_bytes, alive_ratio, shrink_ratio
                );
                if store_usage.alive_ratio < shrink_ratio {
                    shrink_slots_next_batch.insert(store_usage.slot);
                } else {
                    break;
                }
            } else {
                let current_store_size = store.written_bytes();
                let after_shrink_size = store_usage.alive_bytes_after_shrink;
                let bytes_saved = current_store_size.saturating_sub(after_shrink_size);
                total_bytes -= bytes_saved;
                shrink_slots.insert(store_usage.slot, Arc::clone(store));
            }
        }
        (shrink_slots, shrink_slots_next_batch)
    }
```

**File:** accounts-db/src/accounts_db.rs (L5045-5056)
```rust
    /// Determines whether a given AccountStorageEntry instance is a
    /// candidate for shrinking.
    pub(crate) fn is_candidate_for_shrink(&self, store: &AccountStorageEntry) -> bool {
        let total_bytes = store.written_bytes();
        let alive_bytes = self.alive_bytes_after_shrink(store) as u64;
        match self.shrink_ratio {
            AccountShrinkThreshold::TotalSpace { shrink_ratio: _ } => alive_bytes < total_bytes,
            AccountShrinkThreshold::IndividualStore { shrink_ratio } => {
                (alive_bytes as f64 / total_bytes as f64) < shrink_ratio
            }
        }
    }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L2993-3061)
```rust
#[test]
fn test_select_candidates_by_total_usage_2_way_split_condition() {
    // three candidates, 2 are selected for shrink, one is ignored
    let db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let mut candidates = ShrinkCandidates::default();

    let (_temp_dirs, common_store_path) = get_temp_accounts_paths(1).unwrap();
    let account_size = 100;
    let store_file_size = account_size + 10_000;
    let account = AccountSharedData::new(1, account_size as usize, &Pubkey::default());

    let store1_slot = 11;
    let store1 = Arc::new(AccountStorageEntry::new(
        &common_store_path[0],
        store1_slot,
        store1_slot as AccountsFileId,
        store_file_size,
        db.accounts_file_provider,
    ));
    store1
        .accounts
        .write_accounts(&(store1_slot, [(&Pubkey::new_unique(), &account)].as_slice()));
    db.storage.insert(Arc::clone(&store1));
    store1.num_alive_bytes.store(0, Ordering::Release);
    candidates.insert(store1_slot);

    let store2_slot = 22;
    let store2 = Arc::new(AccountStorageEntry::new(
        &common_store_path[0],
        store2_slot,
        store2_slot as AccountsFileId,
        store_file_size,
        db.accounts_file_provider,
    ));
    store2
        .accounts
        .write_accounts(&(store2_slot, [(&Pubkey::new_unique(), &account)].as_slice()));
    db.storage.insert(Arc::clone(&store2));
    store2
        .num_alive_bytes
        .store(store2.written_bytes() as usize / 2, Ordering::Release);
    candidates.insert(store2_slot);

    let store3_slot = 33;
    let store3 = Arc::new(AccountStorageEntry::new(
        &common_store_path[0],
        store3_slot,
        store3_slot as AccountsFileId,
        store_file_size,
        db.accounts_file_provider,
    ));
    store3
        .accounts
        .write_accounts(&(store3_slot, [(&Pubkey::new_unique(), &account)].as_slice()));
    db.storage.insert(Arc::clone(&store3));
    store3
        .num_alive_bytes
        .store(store3.written_bytes() as usize, Ordering::Release);
    candidates.insert(store3_slot);

    // Set the target ratio to default (0.8), both store1 and store2 must be selected and store3 is ignored.
    let target_alive_ratio = DEFAULT_ACCOUNTS_SHRINK_RATIO;
    let (selected_candidates, next_candidates) =
        db.select_candidates_by_total_usage(&candidates, target_alive_ratio);
    assert_eq!(2, selected_candidates.len());
    assert!(selected_candidates.contains(&store1_slot));
    assert!(selected_candidates.contains(&store2_slot));
    assert_eq!(0, next_candidates.len());
}
```
