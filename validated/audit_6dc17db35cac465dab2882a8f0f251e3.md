### Title
Unrandomized top-level bucket selection in `BucketMap::bucket_ix` allows an attacker to deterministically concentrate accounts into a single on-disk index bucket, causing disproportionate storage and CPU cost - (File: `bucket_map/src/bucket_map.rs`)

### Summary
The external report's bug class is: a deterministic, publicly-computable identifier (a CREATE2-style salt derived only from public inputs) lets an unprivileged actor pre-determine and manipulate which underlying resource slot will be used, before/independent of the legitimate controller's intended flow. The Solana analog in the accounts index's on-disk bucket map is `BucketMap::bucket_ix`, which selects which of the `N` on-disk index buckets a `Pubkey` is routed to using only the raw, unsalted leading bits of the pubkey [1](#0-0) . Unlike the intra-bucket slot hash (`Bucket::bucket_index_ix`), which intentionally mixes in a per-node random seed specifically to prevent an attacker from deterministically clustering pubkeys into one place [2](#0-1) , the top-level bucket selection has no such protection.

### Finding Description
`BucketMap::bucket_ix` computes the destination bucket purely from the pubkey's raw bytes: [3](#0-2) 

This function reads the first 8 bytes of the pubkey and right-shifts them to extract the top `max_buckets_pow2` bits, using that value directly as the bucket index — with no random salt and no cluster-wide/per-node randomization. `get_bucket` (used by `update`, `insert`, and other index mutation paths) relies on this deterministic mapping: [4](#0-3) 

By contrast, the same file's `Bucket::bucket_index_ix` — used to place an entry *within* a given bucket — explicitly mixes in a locally-generated random value specifically to defeat exactly this kind of attack, as the surrounding comment documents: [2](#0-1) 
That protection does not extend to the coarser, first-level bucket selection performed by `bucket_ix`.

Because Solana pubkeys used for regular (non-PDA) accounts are Ed25519 public keys that a user can grind for (vanity address generation is a well-established, cheap technique already used by wallets/tooling), and because PDA addresses are derived via `find_program_address`, which iterates a bump seed until `create_program_address` produces an address off the Ed25519 curve — giving a caller-controlled seed a large amount of freedom over the resulting bit pattern — an attacker can cheaply search for many pubkeys (or PDA seeds) whose leading bits collide into the same value of `location >> (u64::BITS - max_buckets_pow2)`. With a typical `max_buckets_pow2` in the range of thousands to tens of thousands of buckets, matching bucket-selection bits requires only trivial grinding (a handful of bits), not searching over the full 256-bit key space.

By funding accounts at these ground pubkeys/PDAs (paying only normal rent), the attacker forces a disproportionate number of live accounts into a single on-disk index `Bucket`. That bucket's disk-backed hash table (`BucketStorage`) then has to grow far more than the others to accommodate a skewed load factor, and every lookup, insert, and linear probe within it (`bucket_find_index_entry`, `find_index_entry_mut`, `bucket_create_key`) has to scan an unusually large `max_search` window because far more of the map's total working set is concentrated in one bucket [5](#0-4) . This produces amplified disk I/O, mmap growth, and CPU time on the single hot shard relative to the rest of the accounts index, for a fraction of the cost the attacker pays (ordinary rent-exempt account creation), which is a disproportionate storage/CPU cost condition.

### Impact Explanation
This is a resource-exhaustion/DoS-style vector localized to the accounts index's disk bucket subsystem: an attacker can cheaply and deterministically overload a specific bucket in the accounts index, causing outsized disk growth, more frequent bucket resizing, and slower index operations for that shard, degrading validator index performance disproportionately to the attacker's cost. It does not directly corrupt consensus state (each `Pubkey`→value mapping remains correct once the bucket has room), but it is a "disproportionate storage and CPU cost" issue as defined in the validation criteria.

### Likelihood Explanation
Moderate-to-low. It requires deliberate, sustained grinding of pubkeys/PDAs whose top bits collide with a target bucket index (this is a similar order of computational effort as generating short vanity addresses), plus paying rent for a large volume of accounts at those addresses to build up the skew. This is achievable by a motivated attacker but is not free, and is bounded by rent economics and per-block account-creation throughput, similar in spirit to the original report's likelihood rating of 2/10.

### Recommendation
Mix a per-node/per-cluster random value into `BucketMap::bucket_ix`'s bucket-selection computation (analogous to the `random` seed already used in `Bucket::bucket_index_ix`), so that top-level bucket placement cannot be predicted or targeted by an external actor grinding pubkeys or PDA seeds. Alternatively/additionally, apply a keyed hash (e.g., `ahash::RandomState` with a locally generated seed) to the pubkey before extracting the bucket-selection bits, rather than using the raw leading bytes directly.

### Proof of Concept
Conceptually (cannot be executed here, but derivable from the code):
1. Read `max_buckets_pow2` for a target validator's accounts index configuration (a small, low number of bits, e.g. 12–16, depending on configured bin count).
2. Grind Ed25519 keypairs (or PDA seeds via `find_program_address`) until finding a large set of pubkeys whose first `max_buckets_pow2` bits (per `read_be_u64` in `bucket_map.rs` lines 192–207) all match a chosen target value — this is computationally comparable to typical vanity-address grinding and does not require breaking Ed25519.
3. Submit ordinary `CreateAccount`/rent-paying transactions funding many accounts at these ground addresses.
4. Because `BucketMap::get_bucket`/`bucket_ix` route all of these pubkeys to the identical on-disk bucket, that specific `Bucket`'s `BucketStorage` will need repeated `grow`/resize operations (see `Bucket::grow`, `bucket_create_key`, `find_index_entry_mut` in `bucket_map/src/bucket.rs`) while other buckets remain sparsely populated, producing an observably skewed, attacker-controlled distribution of index storage and CPU cost across buckets — directly mirroring the report's "anyone can pre-compute/target a resource before or independent of the legitimate assignment process" bug class, but manifesting in the accounts index's bucket-map sharding logic rather than in a smart-contract token factory.

### Citations

**File:** bucket_map/src/bucket_map.rs (L176-190)
```rust
    /// Update Pubkey `key`'s value with function `updatefn`
    pub fn update<F>(&self, key: &Pubkey, updatefn: F)
    where
        F: FnMut(Option<(&[T], RefCount)>) -> Option<(Vec<T>, RefCount)>,
    {
        self.get_bucket(key).update(key, updatefn)
    }

    pub fn get_bucket(&self, key: &Pubkey) -> &Arc<BucketApi<T>> {
        self.get_bucket_from_index(self.bucket_ix(key))
    }

    pub fn get_bucket_from_index(&self, ix: usize) -> &Arc<BucketApi<T>> {
        &self.buckets[ix]
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

**File:** bucket_map/src/bucket.rs (L259-297)
```rust
    fn bucket_find_index_entry(
        index: &BucketStorage<IndexBucket<T>>,
        key: &Pubkey,
        random: u64,
    ) -> Option<(IndexEntryPlaceInBucket<T>, u64)> {
        let ix = Self::bucket_index_ix(key, random) % index.capacity();
        for i in ix..ix + index.max_search() {
            let ii = i % index.capacity();
            if index.is_free(ii) {
                continue;
            }
            let elem = IndexEntryPlaceInBucket::new(ii);
            if elem.key(index) == key {
                return Some((elem, ii));
            }
        }
        None
    }

    fn bucket_create_key(
        index: &mut BucketStorage<IndexBucket<T>>,
        key: &Pubkey,
        random: u64,
        is_resizing: bool,
    ) -> Result<u64, BucketMapError> {
        let ix = Self::bucket_index_ix(key, random) % index.capacity();
        for i in ix..ix + index.max_search() {
            let ii = i % index.capacity();
            if !index.is_free(ii) {
                continue;
            }
            index.occupy(ii, is_resizing).unwrap();
            // These fields will be overwritten after allocation by callers.
            // Since this part of the mmapped file could have previously been used by someone else, there can be garbage here.
            IndexEntryPlaceInBucket::new(ii).init(index, key);
            return Ok(ii);
        }
        Err(BucketMapError::IndexNoSpace(index.contents.capacity()))
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
