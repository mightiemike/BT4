### Title
Disk-backed AccountsIndex `BucketMap` selects the top-level bucket using an unkeyed, network-wide deterministic function of the pubkey, allowing bucket-targeted grinding attacks - (File: `bucket_map/src/bucket_map.rs`)

### Summary
`BucketMap::bucket_ix()` chooses which top-level on-disk index bucket a pubkey is routed to purely from the raw high-order bits of the pubkey, with no per-node randomization. This is the same bug class as the AI Arena finding: a deterministic function is fed attacker-controlled input (here, a `Pubkey`, analogous to the attacker-supplied `dna`), so an attacker can pre-compute/grind inputs that always land in one specific "bucket," subverting an invariant the codebase elsewhere explicitly tries to protect against.

### Finding Description
`BucketMap::bucket_ix()` computes the destination bucket as: [1](#0-0) 

which calls `read_be_u64()`, a pure, unsalted function of the first 8 bytes of the `Pubkey`: [2](#0-1) 

This is the *only* step that determines which `Bucket<T>` (and therefore which physical mmap'd index/data files) an account's index entry lives in; `BucketMap::insert/read_value/delete_key/update` all route through `get_bucket(key)` → `bucket_ix(key)`: [3](#0-2) 

By contrast, the *within-bucket* slot-search hash explicitly mixes in a per-node random seed specifically to defeat this class of attack, as the code comments state: [4](#0-3) 

and the analogous in-memory `AccountsIndex` bin calculator (`PubkeyBinCalculator`, used for the in-mem index bins backing this same `BucketMap`) deliberately randomizes its byte/bit read-offset per process instantiation, with a comment noting it explicitly skips the pubkey ranges "most common to grind": [5](#0-4) 

So two closely related components in the same subsystem (in-memory bin selection vs. on-disk top-level bucket selection) apply inconsistent protection: `PubkeyBinCalculator` randomizes its read window to prevent grinding, but `BucketMap::bucket_ix()` does not randomize at all — it always reads the same fixed 8 bytes and shifts by `max_buckets_pow2`, which is identical and predictable across every validator that uses the same `max_buckets` configuration.

An attacker fully controls which pubkeys populate the accounts index: via `solana-keygen`-style vanity-address grinding for wallet accounts, or by grinding PDA seeds/bump values for an attacker-owned program (PDA derivation is a simple deterministic hash the attacker can search offline). Either technique lets the attacker cheaply pre-compute large numbers of pubkeys whose leading bits all collide to the same `bucket_ix()` value, then fund/create accounts for those pubkeys on-chain.

### Impact Explanation
Because `bucket_ix()` is deterministic and unsalted, an attacker can force a disproportionate number of accounts into a single physical bucket file while leaving the rest of the buckets sparse. This causes that specific bucket's index/data files to grow far beyond their fair share, increasing the frequency of `grow_data`/`grow_index` resizes, `max_search` linear-probe cost, and mmap file size for that one bucket, while the overall `BucketMap` capacity planning (sized for uniform distribution across `max_buckets`) is defeated — i.e., disproportionate storage and CPU cost per the accepted impact categories. Because the selection function is identical on every validator (no per-node randomization), the imbalance is reproduced identically network-wide rather than being isolated/mitigated by node-specific randomness, unlike the in-bucket search step or the in-memory `PubkeyBinCalculator`.

### Likelihood Explanation
Likelihood is moderate-to-high for a determined attacker: pubkey/PDA grinding is a standard, cheap offline computation (only SHA-256/ed25519 operations), and the attacker only needs enough SOL to fund rent-exempt accounts for the pubkeys they choose to actually land on-chain. No special validator/operator privileges are required — this is exploitable by any unprivileged user who can submit `CreateAccount`/`CreateAccountWithSeed`/PDA-owning-program transactions.

### Recommendation
Mix a per-process/per-cluster random seed (similar to `Bucket::random` used in `bucket_index_ix`, or the random offset used by `PubkeyBinCalculatorBuilder::with_bins()`) into `BucketMap::bucket_ix()`'s bucket selection, rather than using a fixed, unsalted prefix of the raw pubkey bytes. This closes the asymmetry with the in-memory bin calculator and prevents attacker pre-computation of bucket-colliding pubkeys across all validators.

### Proof of Concept
Conceptual PoC (grinding path, no cluster mutation needed to prove the flaw):
1. Compute `max_buckets_pow2` for a target `AccountsDb` configuration (a public constant/config value).
2. Offline, grind vanity pubkeys (via `solana-keygen grind` or PDA `find_program_address` seed search) such that `read_be_u64(pubkey) >> (64 - max_buckets_pow2)` all equal the same value — this is exactly the computation in `bucket_ix()`/`read_be_u64()`.
3. Fund and create N such accounts on-chain (or as PDAs owned by an attacker-controlled program).
4. All N accounts route to the same `Bucket<T>` via `BucketMap::get_bucket()`, causing repeated `grow_index`/`grow_data` calls and mmap growth concentrated in one bucket, verifiable via `BucketMapStats` counters exposed by `bucket_map/src/bucket_stats.rs`, while sibling buckets remain near-empty. [6](#0-5) [4](#0-3) [7](#0-6)

### Citations

**File:** bucket_map/src/bucket_map.rs (L156-186)
```rust
    /// Get the values for Pubkey `key`
    pub fn read_value<C: for<'a> From<&'a [T]>>(&self, key: &Pubkey) -> Option<(C, RefCount)> {
        self.get_bucket(key).read_value(key)
    }

    /// Delete the Pubkey `key`
    pub fn delete_key(&self, key: &Pubkey) {
        self.get_bucket(key).delete_key(key);
    }

    /// Update Pubkey `key`'s value with 'value'
    pub fn insert(&self, key: &Pubkey, value: (&[T], RefCount)) {
        self.get_bucket(key).insert(key, value)
    }

    /// Update Pubkey `key`'s value with 'value'
    pub fn try_insert(&self, key: &Pubkey, value: (&[T], RefCount)) -> Result<(), BucketMapError> {
        self.get_bucket(key).try_write(key, value)
    }

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

**File:** accounts-db/src/pubkey_bins.rs (L120-136)
```rust
impl PubkeyBinCalculatorBuilder {
    /// Builds a `PubkeyBinCalculator` with `num_bins`.
    ///
    /// The returned bin calculator will produce *unique* mappings
    /// compared to other bin calculators!
    ///
    /// # Panics
    ///
    /// This function will panic if the following conditions are not met:
    /// * `num_bins` must be a power of two
    /// * `num_bins` must be <= 2^25
    pub fn with_bins(num_bins: NonZeroUsize) -> PubkeyBinCalculator {
        // Skip the beginning and end of the pubkey range, which is the most common to grind.
        const SKIP: usize = 16;
        let offset = rng().random_range(SKIP..=(MAX_OFFSET - SKIP));
        Self::with_bins_and_offset(num_bins, offset)
    }
```
