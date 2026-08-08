### Title
Concurrent disk-index bucket growth races on `Reallocated::active_reallocations`, causing validator panic - ([File: bucket_map/src/bucket.rs])

### Summary
The bug report's root cause is a mutable "in-progress" state flag that is set on one code path but not safely reset/serialized before a second concurrent operation checks it, causing a downstream failure. The closest reachable analog in agave's AccountsDb bucket map (the on-disk accounts index) is `Reallocated<I, D>::add_reallocation()` in [1](#0-0) , which unconditionally asserts that no reallocation is already pending. This assert can be reached concurrently because `BucketApi::grow()` only takes a **read** lock on the bucket [2](#0-1) , while the flag is only cleared under a **write** lock in `get_write_bucket()` → `handle_delayed_grows()` [3](#0-2) [4](#0-3) .

### Finding Description
`Reallocated::add_reallocation()` enforces the invariant "only one reallocation can be pending" via an atomic `fetch_add` + assert: [1](#0-0) 

The flag is cleared only by `get_reallocated()` (a `compare_exchange(1, 0, ...)`), which is invoked from `handle_delayed_grows()`, itself only called while holding the bucket's **write** lock (`get_write_bucket()`): [3](#0-2) 

However, `BucketApi::grow()` — the entry point used by callers such as `InMemAccountsIndex::write_to_disk()` (`accounts-db/src/accounts_index/in_mem_accounts_index.rs`) when a `try_write` fails with "no space" — only acquires a **read** lock before calling `bucket.grow(err)`: [2](#0-1) 

Because `RwLock` permits multiple concurrent readers, two (or more) threads that each independently fail a `try_write` on the *same* bucket (e.g., two different pubkeys hashing into the same bucket index, both needing more space) can call `grow()` at the same time. Each call path (`grow_data`/`grow_index` → `add_reallocation()`) increments `active_reallocations` from 0 to 1 under the assumption that it was the sole caller. If the first caller's `handle_delayed_grows()` (which requires the write lock and therefore serializes with other writers, but not with concurrent readers calling `grow()`) has not yet run before the second caller's `add_reallocation()` executes, the assert `"Only 1 reallocation can occur at a time"` fires.

This mirrors the reported bug class exactly: a state flag ("burn type" in the Solidity report, "active_reallocations" here) that is supposed to gate an operation is not reliably serialized against concurrent legitimate operations, and the mismatch triggers a hard failure (a `revert` in Solidity; a Rust `panic!`/`assert!` here).

### Impact Explanation
`assert!` panics in Rust abort the running thread (and, depending on panic strategy/context, the whole validator process). The `InMemAccountsIndex` disk-backed bucket map is exercised continuously by ordinary account writes/flushes/inserts driven by normal transaction processing — no privileged or validator-operator action is required. If the assert fires on a validator's background flush/insert thread, this is a node panic — one of the concrete accepted impacts (node panic) for AccountsDB storage/index/bucket-map issues in scope.

### Likelihood Explanation
Likelihood depends on: (a) enough concurrent index-insert throughput to have two threads hit `DataNoSpace`/`IndexNoSpace` on the *same* bucket at nearly the same time, and (b) the write-lock-protected `handle_delayed_grows()` not running between the two `grow()` calls. Under high account churn (heavy transaction load creating many new accounts), disk-index buckets are grown routinely, and the growth path is triggered from multiple insert call sites (`Bucket::insert`, `Bucket::batch_insert_non_duplicates`, `InMemAccountsIndex::write_to_disk`) that can run concurrently across the thread pool. This makes the race plausible under sustained load, though I was not able to fully trace every call site's locking discipline within the available context, so I cannot definitively rule out an additional external synchronization mechanism (e.g., a per-bucket dispatch guaranteeing only one writer thread touches a given bucket at a time) that would make this unreachable in practice.

### Recommendation
Ensure `add_reallocation()`/`grow()` cannot race with itself: either (1) have `BucketApi::grow()` take the bucket's **write** lock (matching `get_write_bucket()`), so growth attempts are fully serialized with `handle_delayed_grows()`, or (2) make `add_reallocation()` idempotent/non-panicking when a reallocation is already pending (e.g., skip redundant regrowth instead of asserting), and instead surface a retry so callers loop until the delayed grow has been applied.

### Proof of Concept
Not independently reproduced; this is a static-analysis finding based on tracing lock acquisition:
1. `InMemAccountsIndex::write_to_disk()` loops on `disk.try_write(...)`, calling `disk.grow(err)` on failure (`accounts-db/src/accounts_index/in_mem_accounts_index.rs`).
2. `BucketApi::grow()` acquires only `self.bucket.read()` before calling `Bucket::grow()` [2](#0-1) .
2. `Bucket::grow()` → `grow_data`/`grow_index` → `Reallocated::add_reallocation()` asserts `active_reallocations == 0` before setting it to 1 [1](#0-0) .
3. Two threads inserting different pubkeys that hash into the same bucket and both need more space can enter step 2/3 concurrently (both holding read locks), since nothing prevents the second thread's `add_reallocation()` from executing before the first's `handle_delayed_grows()` (write lock) clears the flag — resulting in the assert firing and the thread panicking.

### Citations

**File:** bucket_map/src/bucket.rs (L67-75)
```rust
impl<I: BucketOccupied, D: BucketOccupied> Reallocated<I, D> {
    /// specify that a reallocation has occurred
    pub fn add_reallocation(&self) {
        assert_eq!(
            0,
            self.active_reallocations.fetch_add(1, Ordering::Relaxed),
            "Only 1 reallocation can occur at a time"
        );
    }
```

**File:** bucket_map/src/bucket.rs (L845-859)
```rust
    /// if a bucket was resized previously with a read lock, then apply that resize now
    pub fn handle_delayed_grows(&mut self) {
        if self.reallocated.get_reallocated() {
            // swap out the bucket that was resized previously with a read lock
            let mut items = std::mem::take(&mut *self.reallocated.items.lock().unwrap());

            if let Some(bucket) = items.index.take() {
                self.apply_grow_index(bucket);
            } else {
                // data bucket
                let (i, new_bucket) = items.data.take().unwrap();
                self.apply_grow_data(i as usize, new_bucket);
            }
        }
    }
```

**File:** bucket_map/src/bucket_api.rs (L103-111)
```rust
    fn get_write_bucket(&self) -> RwLockWriteGuard<'_, Option<Bucket<T>>> {
        let mut bucket = self.bucket.write().unwrap();
        if let Some(bucket) = bucket.as_mut() {
            bucket.handle_delayed_grows();
        } else {
            self.allocate_bucket(&mut bucket);
        }
        bucket
    }
```

**File:** bucket_map/src/bucket_api.rs (L118-124)
```rust
    pub fn grow(&self, err: BucketMapError) {
        // grows are special - they get a read lock and modify 'reallocated'
        // the grown changes are applied the next time there is a write lock taken
        if let Some(bucket) = self.bucket.read().unwrap().as_ref() {
            bucket.grow(err)
        }
    }
```
