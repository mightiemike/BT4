### Title
`PubkeyBinCalculator` bins accounts by raw pubkey bytes instead of a hash, enabling grindable hot-bin/lock contention - (File: accounts-db/src/pubkey_bins.rs)

### Summary
The external report's bug class is: using a raw/unhashed value where a well-distributed hash is expected, which lets an attacker bias which "bucket" data lands in. The `AccountsIndex` bin assignment in `PubkeyBinCalculator::bin_from_pubkey` exhibits the same pattern: it reads raw bytes directly out of the account `Pubkey` (which is fully attacker-controllable via keypair grinding) instead of hashing the pubkey, to decide which index bin (and therefore which lock/shard) an account's index entry lives in.

### Finding Description
`PubkeyBinCalculator::bin_from_pubkey` computes the bin for a pubkey by reading a fixed 4-byte window directly out of the pubkey bytes and masking/shifting it — no cryptographic hash is applied: [1](#0-0) [2](#0-1) 

Because a `Pubkey` is just an ed25519 public key that an attacker fully controls the byte content of (via offline keypair generation / grinding, e.g. vanity-address tooling that already exists in the Solana ecosystem), an attacker can pre-compute large numbers of keypairs whose pubkeys all read the same bits at the fixed `byte_offset`/`bit_offset` used by the bin calculator, and therefore all land in the same `AccountsIndex` bin. This is the same root-cause pattern as the reported bug class: a value that should be derived through a hash function (to guarantee uniform distribution / attacker-resistance) is instead taken directly from raw, attacker-influenceable bytes.

This differs from the sibling `bucket_map/src/bucket.rs::bucket_index_ix`, which correctly runs the pubkey through `ahash::RandomState` (a keyed hash with a runtime-random seed) before deriving a bucket index: [3](#0-2) 
That code even comments explicitly that the randomized hash exists to prevent an attacker from deterministically clustering pubkeys into one location. `PubkeyBinCalculator`, used for the higher-level in-memory/disk `AccountsIndex` bin sharding, has no equivalent protection — it is a bare bit-extraction of the pubkey.

### Impact Explanation
Each `AccountsIndex` bin is a shard with its own lock/`RwLock`-protected map and, in the on-disk mode, its own bucket-map storage. If many pubkeys are ground to collide into a single bin, that bin's map/lock becomes a hot spot: lookups, inserts, and scans for that bin degrade (O(n) growth per hot bin instead of amortized O(1) across evenly spread bins), and lock contention on that bin serializes otherwise-parallel account processing. This produces a disproportionate CPU/latency cost during transaction processing, account loading, and index-bin-parallel operations such as `calculate_accounts_lt_hash_at_startup_from_index`, which iterates `self.accounts_index.account_maps` in parallel per bin: [4](#0-3) 
If one bin holds a disproportionate share of all live accounts (all attacker-created), that parallel fold degenerates toward a single-threaded workload for that bin while other threads finish early, directly causing disproportionate CPU cost with attacker-supplied, unprivileged input (creating accounts with chosen pubkeys is an ordinary unprivileged user action).

### Likelihood Explanation
Exploitation requires only the ability to create many accounts owned by pubkeys the attacker chooses — an entirely unprivileged, permissionless capability on Solana (anyone can generate/airdrop-fund an account for any keypair they hold). Grinding a 32-bit (or fewer, depending on `bit_offset`) collision window offline is computationally cheap compared to grinding a full vanity address, since only `~log2(num_bins)` bits need to match, and this can be done completely off-chain/off-cluster with no rate limiting, so likelihood of an adversary constructing enough colliding pubkeys is moderate-to-high, bounded mainly by how many accounts the attacker is willing to fund/create.

### Recommendation
Derive the bin index from a keyed/randomized hash of the pubkey (e.g., reuse the `ahash::RandomState`-with-runtime-seed approach already used in `bucket_map/src/bucket.rs::bucket_index_ix`) rather than reading raw pubkey bytes directly, so that bin assignment cannot be predicted or targeted by an attacker who controls the pubkey.

### Proof of Concept
Conceptual PoC (not run, since this requires the full `agave` repo and grinding tooling not available in this static analysis):
1. Fix `PubkeyBinCalculator`'s `byte_offset`/`bit_offset`/`mask` for a given `num_bins` configuration (as constructed via `PubkeyBinCalculatorBuilder::with_bins`).
2. Offline, grind keypairs such that `read_bytes(pubkey) >> bit_offset & mask` all equal the same bin value — this only requires matching `log2(num_bins)` bits (as little as ~25 bits max, often far fewer for realistic bin counts), which is tractable with commodity hardware.
3. Fund/create many accounts using these ground pubkeys.
4. Observe that all of these accounts land in the same `AccountsIndex` bin, causing that bin's lock/map to become a bottleneck relative to the evenly-distributed case, visible as elevated per-bin CPU/latency in index operations such as `calculate_accounts_lt_hash_at_startup_from_index`.

Note: I could not fully verify the exact default `num_bins`/`byte_offset` configuration used in production validator startup (e.g., in `accounts_index.rs` / CLI defaults) within the indexed context available to me; a background Devin session with full repo/terminal access would be needed to confirm the exact bit width required for a practical grinding attack and to build a working benchmark PoC.

### Citations

**File:** accounts-db/src/pubkey_bins.rs (L58-71)
```rust
impl PubkeyBinCalculator {
    /// Calculates the bin that `pubkey` maps to.
    #[inline]
    pub fn bin_from_pubkey(&self, pubkey: &Pubkey) -> usize {
        // This debug assert checks that enough was read from the pubkey to calculate the bin.
        // The number of bits for num_bins + number of bits for bit_offset
        // *must* be <= number of bits read from the pubkey.
        debug_assert!((self.mask + 1).ilog2() + self.bit_offset as u32 <= ReadBytesType::BITS);
        let bytes = self.read_bytes(pubkey);
        let bin = (bytes >> self.bit_offset) & self.mask;
        // SAFETY: bin is a u32, which can fit in a usize
        // (Unfortunately the trait `std::convert::From<u32>` is not implemented for `usize`)
        bin as usize
    }
```

**File:** accounts-db/src/pubkey_bins.rs (L73-101)
```rust
    /// Read the bytes from `pubkey` needed to calculate the bin.
    #[inline]
    fn read_bytes(&self, pubkey: &Pubkey) -> ReadBytesType {
        debug_assert!(self.byte_offset <= MAX_BYTE_OFFSET);
        let ptr = pubkey.as_array().as_ptr();
        // Because we know we're reading valid bytes within the pubkey, unsafe is used
        // to avoid bounds checks that would occur if reading via slice indexing.
        //
        // SAFETY:
        //
        // - `byte_offset` was checked at construction to be in range to read a ReadBytesType.
        //
        // add() is safe:
        // - `byte_offset` can fit in an isize.
        // - `byte_offset` is in-range of `pubkey`.
        //
        // read_unaligned() is safe:
        // - the ptr being read is valid
        //   - the ptr came from `pubkey`.
        //   - the memory range being read is entirely contained within the
        //     bounds of the allocation (this was checked above by `add()`).
        // - the value of the type being read (ReadBytesType) is valid
        //   - the memory of `pubkey` has been initialized
        unsafe {
            ptr.add(self.byte_offset)
                .cast::<ReadBytesType>()
                .read_unaligned()
        }
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

**File:** accounts-db/src/accounts_db.rs (L4658-4666)
```rust
        let mut lt_hash = self
            .accounts_index
            .account_maps
            .par_iter()
            .fold(
                LtHash::identity,
                |mut accumulator_lt_hash, accounts_index_bin| {
                    for pubkey in accounts_index_bin.keys() {
                        let account_lt_hash = self
```
