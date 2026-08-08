### Title
Concurrent bucket-map index/data growth can violate the single-active-reallocation invariant and panic the validator - ([File: bucket_map/src/bucket.rs])

### Summary
`Bucket::grow()`/`BucketApi::grow()` in the accounts-index bucket map is designed to be callable while only holding a **read** lock on the bucket (the comment on `BucketApi::grow` explicitly says "grows are special - they get a read lock and modify 'reallocated'"), and the actual application of the grow is deferred until the next write lock is taken. The invariant that only one reallocation may be pending at a time is enforced purely by an `assert_eq!` inside `Reallocated::add_reallocation`, not by any lock that actually excludes concurrent callers of `grow()`. If accounts-index insert/update traffic drives two independent `BucketMapError` (`IndexNoSpace`/`DataNoSpace`) events for the same bucket before the first pending grow is consumed by `handle_delayed_grows()`, both callers can enter `add_reallocation()` concurrently and hit the assertion, panicking the thread.

### Finding Description
`Reallocated::add_reallocation` asserts exclusivity: [1](#0-0) 

`BucketApi::grow` takes only a **shared read lock** on the bucket before calling into `Bucket::grow`, explicitly noting that the resulting reallocation is deferred and applied only later, under a write lock: [2](#0-1) 

`Bucket::grow` dispatches to `grow_data`/`grow_index`, both of which call `self.reallocated.add_reallocation()` while `self` is only borrowed immutably (i.e., while other readers of the same bucket can be concurrently active): [3](#0-2) 

The pending reallocation is only consumed (and the `active_reallocations` counter reset) inside `handle_delayed_grows`, which itself is only invoked when a **write** lock on the bucket is subsequently acquired (e.g., via `BucketApi::get_write_bucket`): [4](#0-3) [5](#0-4) 

The intended flow for a single inserting thread is: acquire write lock → `try_write` fails with a resize error → release write lock → call `grow(err)` under a **read** lock, which sets `active_reallocations` from 0 to 1 → on next write-lock acquisition, `handle_delayed_grows` resets it to 0 and applies the grow. This is safe for a single thread operating serially. However, the bucket map is shared across the accounts index and is accessed by multiple threads performing concurrent inserts/updates/reads (e.g. during account writes and index maintenance, which is routine, unprivileged validator activity, not an operator-only action). Because `grow()`/`add_reallocation()` only requires a *read* lock, and `RwLock::read()` permits multiple simultaneous readers, nothing in this design actually prevents two different threads — each having independently hit a resize error on the same bucket — from calling `add_reallocation()` concurrently before either one's grow has been consumed by a subsequent writer's `handle_delayed_grows()`. This directly violates the invariant the assertion is meant to enforce ("Only 1 reallocation can occur at a time"), causing the assertion to fail and panic the thread that lost the race.

This is conceptually the same bug class flagged in the external report: work that is expected to be split into sequential, single-outstanding units (Lido's one-at-a-time 500 ETH withdrawal request) is here a resize/grow operation gated by a counter intended to permit only one outstanding unit — except the concurrency model surrounding it (read-lock-only growth, write-lock-deferred consumption) does not actually serialize the producers of that unit, creating a race that the code's own invariant assumes cannot happen.

### Impact Explanation
If the assertion fires, the thread performing the accounts-index update panics. Given that `AccountsIndex`/bucket map operations execute on validator-critical paths (transaction processing, account loads/updates, index maintenance), an uncaught panic here can crash or halt the node — a concrete node panic, which is one of the explicitly accepted impact categories.

### Likelihood Explanation
This requires two independent resize triggers (`IndexNoSpace` or `DataNoSpace`) for the *same* bucket to occur close together in time from different threads, with the first grow not yet consumed via a write-lock `handle_delayed_grows()` call before the second grow call executes `add_reallocation()`. This is plausible under high concurrent account-index insert load causing simultaneous capacity exhaustion in the same bucket, but the exact scheduling window is narrow and depends on lock contention timing that could not be fully traced to the top-level `BucketMap`/`AccountsIndex` call sites within the available investigation. This should be treated as a race-condition candidate requiring further tracing of all call sites of `BucketApi::grow` (in `bucket_map.rs`) to confirm whether any additional external synchronization prevents the race in practice.

### Recommendation
- Make the "only one active reallocation" invariant actually enforced by the locking model rather than by an assertion alone — e.g., have `grow()` attempt to acquire the write lock (or a dedicated grow-mutex) before mutating `active_reallocations`, or use a `compare_exchange` that gracefully handles the "already growing" case (skip/wait) instead of asserting/panicking.
- Replace the `assert_eq!` panic path in `Reallocated::add_reallocation` with non-panicking handling (e.g., return a `bool`/`Result` indicating a reallocation is already pending) so a lost race degrades gracefully instead of crashing the thread.
- Audit all call sites of `BucketApi::grow` in `bucket_map.rs` to confirm whether multiple threads can independently trigger grow on the same bucket, and add regression tests that concurrently drive resize errors on a single bucket from multiple threads.

### Proof of Concept
Not independently reproduced within the scope of this analysis (would require a multi-threaded stress test against a single `Bucket` instance that concurrently drives `IndexNoSpace`/`DataNoSpace` errors via `try_write`/`insert` from two threads and calls `BucketApi::grow` before either thread has taken a write lock to consume the pending reallocation, observing the `assert_eq!` panic in `Reallocated::add_reallocation`). This is flagged as an area needing further dynamic verification.

### Citations

**File:** bucket_map/src/bucket.rs (L68-75)
```rust
    /// specify that a reallocation has occurred
    pub fn add_reallocation(&self) {
        assert_eq!(
            0,
            self.active_reallocations.fetch_add(1, Ordering::Relaxed),
            "Only 1 reallocation can occur at a time"
        );
    }
```

**File:** bucket_map/src/bucket.rs (L802-843)
```rust
    /// grow a data bucket
    /// The application of the new bucket is deferred until the next write lock.
    pub fn grow_data(&self, data_index: u64, current_capacity_pow2: u8) {
        let (new_bucket, _file_name) = BucketStorage::new_resized(
            &self.drives,
            self.index.max_search,
            self.data.get(data_index as usize),
            Capacity::Pow2(std::cmp::max(
                current_capacity_pow2 + 1,
                DEFAULT_CAPACITY_POW2,
            )),
            1 << data_index,
            Self::elem_size(),
            &self.stats.data,
        );
        self.reallocated.add_reallocation();
        let mut items = self.reallocated.items.lock().unwrap();
        items.data = Some((data_index, new_bucket));
    }

    fn bucket_index_ix(key: &Pubkey, random: u64) -> u64 {
        // the locally generated random will make it hard for an attacker
        // to deterministically cause all the pubkeys to land in the same
        // location in any bucket on all validators
        let hasher_builder = ahash::RandomState::with_seeds(random, random, random, random);
        hasher_builder.hash_one(key)
    }

    /// grow the appropriate piece. Note this takes an immutable ref.
    /// The actual grow is set into self.reallocated and applied later on a write lock
    pub(crate) fn grow(&self, err: BucketMapError) {
        match err {
            BucketMapError::DataNoSpace((data_index, current_capacity_pow2)) => {
                //debug!("GROWING SPACE {:?}", (data_index, current_capacity_pow2));
                self.grow_data(data_index, current_capacity_pow2);
            }
            BucketMapError::IndexNoSpace(current_capacity) => {
                //debug!("GROWING INDEX {}", sz);
                self.grow_index(current_capacity);
            }
        }
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
