This confirms the key asymmetry: `PubkeyBinCalculatorBuilder::with_bins()` (used for AccountsIndex's top-level in-memory bin selection) deliberately picks a **random offset** into the pubkey at construction time specifically to prevent grinding attacks [1](#0-0) , and even explicitly skips the pubkey's beginning/end bytes "which is the most common to grind" [2](#0-1) . In contrast, `BucketMap::bucket_ix` (the disk-index bucket-selection function named in the question) uses a **fixed, unrandomized** read of the pubkey's first 8 bytes via `read_be_u64` [3](#0-2) , with no per-process random seed at all — unlike the in-bucket slot hashing `bucket_index_ix`, which explicitly uses a random seed and documents the anti-grinding rationale [4](#0-3) .

### Title
Unrandomized `BucketMap::bucket_ix` allows pubkey-grinding to skew disk-index bucket load - ([File: bucket_map/src/bucket_map.rs])

### Summary
`BucketMap::bucket_ix` selects a bucket purely from the top `max_buckets_pow2` bits of the raw pubkey via `read_be_u64`, with no per-process randomization, unlike the sibling `Bucket::bucket_index_ix` which uses a random `ahash` seed specifically to defeat grinding. An unprivileged attacker can grind pubkeys offline so many collide into the same bucket, then create real accounts at those addresses, concentrating index load into a single `Arc<BucketApi<T>>`/`BucketStorage<IndexBucket<T>>`.

### Finding Description
`BucketMap::get_bucket` -> `bucket_ix(key)` computes `location = read_be_u64(key.as_ref())` and shifts by `u64::BITS - max_buckets_pow2` to select a bucket index [5](#0-4) . This calculation is fully deterministic and identical on every validator/run — no random seed, no per-instance offset, unlike `PubkeyBinCalculatorBuilder::with_bins()` which randomizes the byte offset per process precisely to prevent this class of attack [1](#0-0) , and unlike `Bucket::bucket_index_ix`, which uses `ahash::RandomState::with_seeds(random, ...)` with an explicit comment: "the locally generated random will make it hard for an attacker to deterministically cause all the pubkeys to land in the same location" [4](#0-3) .

`BucketMap` is instantiated per `AccountsIndex` bin (one `BucketMap` per in-memory bin when disk index is enabled) with `max_buckets` equal to the configured bucket count for that bin's on-disk storage [6](#0-5) ; disk index is only active when `IndexLimit::Minimal` or `IndexLimit::Threshold` is configured (not the default `InMemOnly`/"unlimited") [7](#0-6) . An attacker who knows this fixed algorithm can grind vanity pubkeys offline so their top bits collide into a single bucket index, then create/write real on-chain accounts at those addresses. Every `insert`/`update`/`read_value` call for those accounts routes through `get_bucket(key)` -> `bucket_ix` into the same `Arc<BucketApi<T>>`, causing that bucket's `BucketStorage<IndexBucket<T>>` to grow far beyond its statistically expected share, triggering more frequent `Bucket::grow_index` resizes and longer `max_search` linear scans, which also degrades lookups for unrelated legitimate accounts that happen to hash into that same bucket.

### Impact Explanation
Scoped impact: disproportionate storage and CPU cost concentrated in one on-disk index bucket, and collateral read/write latency degradation for legitimate accounts sharing that bucket, matching the "disproportionate storage and CPU cost" bounty category. This does not corrupt data, does not desync consensus, and does not affect the in-memory `PubkeyBinCalculator` bin selection (which is already randomized), so the impact is limited to nodes running with disk-backed accounts index enabled (`--accounts-index-limit` other than `unlimited`).

### Likelihood Explanation
Preconditions: only requires offline pubkey grinding (cheap; ~2^`max_buckets_pow2` grind attempts per matching pubkey, e.g. a few thousand hashes for typical bucket counts) and normal account creation paid for by the attacker. No privileged access needed. However, real economic cost scales with the number of accounts created (rent/fees), and the disk index is opt-in (non-default), which limits practical severity and the population of affected nodes, but does not eliminate the underlying design gap.

### Recommendation
Randomize `BucketMap::bucket_ix` the same way `Bucket::bucket_index_ix` is randomized — e.g., derive the top-level bucket selection from a per-process random seed (hashed via `ahash` or similar) rather than raw `read_be_u64` bytes, or reuse the same random-offset technique as `PubkeyBinCalculatorBuilder`.

### Proof of Concept
Add a unit test in `bucket_map/src/bucket_map.rs` analogous to the existing `test_bucket_index_ix_is_stable` (`bucket_map/src/bucket.rs:1587-1599`) but for `bucket_ix`: assert that for a fixed `max_buckets_pow2`, one can construct N pubkeys with identical top bits (e.g. by setting the first bytes to a constant and randomizing the rest) that all map to the same `bucket_ix` result across repeated `BucketMap::new` instantiations, then contrast with a fuzz test that inserts random vs. grinded pubkey sets into a `BucketMap` with `max_buckets` set to a small power of two, and assert `stats.index.resizes` and per-bucket entry counts stay within a bounded multiple (e.g. 2x) of `num_inserted / num_buckets` for random keys, while showing the grinded set violates that bound by concentrating >90% of entries (and resizes) into a single bucket.

### Citations

**File:** accounts-db/src/pubkey_bins.rs (L131-136)
```rust
    pub fn with_bins(num_bins: NonZeroUsize) -> PubkeyBinCalculator {
        // Skip the beginning and end of the pubkey range, which is the most common to grind.
        const SKIP: usize = 16;
        let offset = rng().random_range(SKIP..=(MAX_OFFSET - SKIP));
        Self::with_bins_and_offset(num_bins, offset)
    }
```

**File:** bucket_map/src/bucket_map.rs (L192-207)
```rust
    /// Get the bucket index for Pubkey `key`
    pub fn bucket_ix(&self, key: &Pubkey) -> usize {
        if self.max_buckets_pow2 > 0 {
            let location = read_be_u64(key.as_ref());
            (location >> (u64::BITS - self.max_buckets_pow2 as u32)) as usize
        } else {
            0
        }
    }
}

/// Look at the first 8 bytes of the input and reinterpret them as a u64
fn read_be_u64(input: &[u8]) -> u64 {
    assert!(input.len() >= std::mem::size_of::<u64>());
    u64::from_be_bytes(input[0..std::mem::size_of::<u64>()].try_into().unwrap())
}
```

**File:** bucket_map/src/bucket.rs (L822-828)
```rust
    fn bucket_index_ix(key: &Pubkey, random: u64) -> u64 {
        // the locally generated random will make it hard for an attacker
        // to deterministically cause all the pubkeys to land in the same
        // location in any bucket on all validators
        let hasher_builder = ahash::RandomState::with_seeds(random, random, random, random);
        hasher_builder.hash_one(key)
    }
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L272-289)
```rust
    pub fn new(bins: usize, config: &AccountsIndexConfig, threads: usize) -> Self {
        const DEFAULT_AGE_TO_STAY_IN_CACHE: Age = 5;
        let ages_to_stay_in_cache = config
            .ages_to_stay_in_cache
            .unwrap_or(DEFAULT_AGE_TO_STAY_IN_CACHE);

        let mut bucket_config = BucketMapConfig::new(bins);
        bucket_config.drives = config.drives.as_ref().cloned();
        bucket_config.restart_config_file = bucket_config
            .drives
            .as_ref()
            .and_then(|drives| drives.first())
            .map(|drive| drive.join("accounts_index_restart"));

        let disk = match config.index_limit {
            IndexLimit::InMemOnly => None,
            IndexLimit::Minimal | IndexLimit::Threshold(_) => Some(BucketMap::new(bucket_config)),
        };
```
