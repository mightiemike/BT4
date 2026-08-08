### Title
Weak entropy in randomized pubkey bin offset enables precomputed bin-collision (hot-bin) grinding attack against the AccountsIndex - (File: accounts-db/src/pubkey_bins.rs)

### Summary
`PubkeyBinCalculatorBuilder::with_bins()` is used to build the `PubkeyBinCalculator` that assigns every account `Pubkey` to one of the `AccountsIndex`'s in-memory bins (the sharding mechanism accounts-db uses to spread accounts across many locks/maps for parallelism). To prevent attackers from being able to pre-grind pubkeys that all land in the same bin, the calculator's byte/bit read offset into the pubkey is supposed to be randomized per validator process. However the actual amount of entropy used for that randomization is tiny, so the "protection" is trivially defeated by precomputation, closely mirroring the reported bug class (a randomness source that looks like it protects against guessing/grinding but in practice offers far less entropy than the nominal design suggests). [1](#0-0) 

### Finding Description
`PubkeyBinCalculator::bin_from_pubkey()` derives a pubkey's bin by reading 4 bytes from the pubkey starting at `byte_offset` and shifting by `bit_offset`, then masking to the number of bins: [2](#0-1) 

The `byte_offset`/`bit_offset` pair (collectively the "offset") is the only secret that stands between an attacker and being able to predict which bin any given pubkey will land in. `PubkeyBinCalculatorBuilder::with_bins()` is documented as needing to "produce *unique* mappings compared to other bin calculators" and explicitly calls out grinding as the threat it defends against: [1](#0-0) 

```rust
pub fn with_bins(num_bins: NonZeroUsize) -> PubkeyBinCalculator {
    // Skip the beginning and end of the pubkey range, which is the most common to grind.
    const SKIP: usize = 16;
    let offset = rng().random_range(SKIP..=(MAX_OFFSET - SKIP));
    Self::with_bins_and_offset(num_bins, offset)
}
```

With `MAX_OFFSET == 231` (defined at [3](#0-2) ) and `SKIP == 16`, the random `offset` is drawn from the range `16..=215` — only **200 possible values**, i.e. roughly 7.6 bits of entropy. This is drastically less entropy than the 256-bit pubkey space the mechanism is meant to protect, echoing the underlying bug class in the report: a randomness source that nominally exists to thwart prediction/grinding but is reduced to a tiny, brute-forceable/enumerable space.

Because the offset space is only 200 values, an attacker does not even need to observe or guess the running validator's specific offset. They can precompute, completely offline and once, 200 sets of colliding pubkeys — one set per possible offset — such that for *whatever* offset a given validator instance happens to randomly select, the attacker already holds a ready-made set of pubkeys that all map into the same bin under that offset. This defeats the purpose of the per-process randomization entirely, since the randomization was intended to make grinding infeasible by making the target offset unknown/unpredictable, but the tiny domain size makes exhaustive precomputation trivial regardless of which offset is actually chosen.

### Impact Explanation
The `AccountsIndex` shards all accounts by bin so that in-memory bin locks/maps can be operated on independently for parallelism (default configuration uses many thousands of bins). If an unprivileged user can economically construct many `Pubkey`s that collide into the same bin (via the precomputed-for-all-200-offsets strategy above), they can cause that bin to become disproportionately large and heavily contended relative to others, which:
- Increases per-bin CPU cost for index operations (lookups, inserts, scans) that are supposed to be evenly distributed across bins.
- Increases lock contention and unbalanced memory usage in the in-memory portion of the index, since one bin absorbs traffic/state meant to be spread across many.

This directly matches the "disproportionate storage and CPU cost" impact category that is explicitly in scope. It does not require any privileged/validator/operator role — any user who can submit ordinary transactions creating accounts with attacker-chosen pubkeys (e.g., via a normal `create_account`-style instruction, or any other mechanism letting them pick a pubkey landing in a chosen bin) can exploit this.

### Likelihood Explanation
Likelihood is limited by two factors that I could not fully resolve with available tooling:
1. **Number of bins in practice** — the default `AccountsIndex` bin count materially affects how easy/valuable a single hot bin is (fewer bins → more natural collisions anyway; the specific default value used in production configuration wasn't confirmed from the snippets retrieved).
2. **Effort to construct colliding pubkeys** — colliding on a 4-byte read at a fixed offset, masked down to `log2(num_bins)` bits, is a lightweight computation (essentially finding pubkeys whose relevant bytes match a target pattern), which is easily within reach of an unprivileged attacker with modest compute, especially since the search domain (200 offsets) is small enough to precompute entirely offline once.

Given the small, enumerable offset space and low cost of pubkey grinding, likelihood is assessed as credible for an attacker motivated to degrade AccountsIndex performance, though the magnitude of real-world impact depends on production bin-count configuration that wasn't independently verified here.

### Recommendation
- Increase the entropy of the offset selection (e.g., draw the offset from the full byte range without an overly restrictive `SKIP`, or better, use a keyed/hashed transform of the pubkey with a securely-generated, full-width (e.g., 64-bit or larger) random key rather than selecting from a ~200-value range) so the number of possible bin-mapping functions is not exhaustible by precomputation.
- Consider using a proper keyed hash function (e.g., `ahash`/`SipHash` with a randomly generated 64-bit+ key, similar in spirit to what `bucket_map::Bucket::bucket_index_ix` does with `ahash::RandomState::with_seeds`) to compute the bin instead of reading raw bytes at a randomized-but-narrow offset, eliminating the small-domain grinding risk entirely.
- Re-evaluate whether `bucket_map::Bucket::bucket_index_ix` (in `bucket_map/src/bucket.rs`), which seeds `ahash::RandomState::with_seeds(random, random, random, random)` with a single repeated 64-bit value instead of four independent values, provides the intended defense-in-depth against hash-flooding; while its 64-bit space is far larger than the 200-value offset issue above and not concretely exploitable with current tooling, using independent seeds would align with the construct's intended security margin.

### Proof of Concept
Conceptual PoC (offline, no special privileges required):
1. For each of the 200 possible `offset` values in `16..=215` (as enumerated by `PubkeyBinCalculatorBuilder::with_bins`), precompute a batch of `Pubkey`s such that the 4-byte read at that `offset`, shifted/masked per `bin_from_pubkey`, maps to a single target bin index (e.g., bin 0). This is a straightforward keyspace search over pubkey bytes.
2. On any running validator, submit ordinary account-creation transactions using pubkeys drawn from the batch corresponding to whichever offset the validator's `PubkeyBinCalculator` happens to have chosen (unknown to the attacker, but irrelevant since all 200 batches were precomputed).
3. Because there are only 200 possible offsets total, the pubkeys used are guaranteed to collide into the target bin regardless of which offset was actually randomly selected at validator startup, producing an artificially hot/oversized bin in the `AccountsIndex` and disproportionate CPU/lock-contention cost for operations touching that bin, relative to a properly load-balanced index. This was validated by direct code review of `bin_from_pubkey`/`with_bins`/`with_bins_and_offset`; a live, end-to-end exploit run against a running validator (to quantify the actual CPU/latency impact) was not performed as part of this analysis. [4](#0-3)

### Citations

**File:** accounts-db/src/pubkey_bins.rs (L9-41)
```rust
// The bin calculator assumes a pubkey is 32 bytes, so enforce that here.
const _: () = assert!(PUBKEY_BYTES == 32);

const BITS_PER_BYTE: usize = u8::BITS as usize;
const _: () = assert!(BITS_PER_BYTE == 8);

/// The maximum number of bins we can support.
///
/// This is based on the number of bytes we read in `read_bytes()`.
///
/// Basically, if we read four bytes (32 bits) from the pubkey as it's "hash",
/// and can have a maximum bit-offset of seven,
/// then the maximum number of bins, as pow2, is 32 - 7 == 25.
///
/// 2^25 bins is over 33 million bins, and that should be more than enough.
/// If we do ever need more, then changing ReadBytesType to u64 gets us 2^57 bins.
///
/// To get the real number, do `pow2(MAX_BINS_POW2)`.
const MAX_BINS_POW2: usize = (size_of::<ReadBytesType>() - 1) * BITS_PER_BYTE + 1;
const _: () = assert!(MAX_BINS_POW2 == 25);

/// The maximum offset we can support.
///
/// This is based on the maximum number of bins.
/// Take the number of bits in a pubkey (256) and subtract the number of bits for max bins (25).
const MAX_OFFSET: usize = PUBKEY_BYTES * BITS_PER_BYTE - MAX_BINS_POW2;
const _: () = assert!(MAX_OFFSET == 231);

/// The bin calculator's byte_offset must be <= this value.
///
/// This ensures we can read enough bytes from the pubkey to calculate the bin.
const MAX_BYTE_OFFSET: usize = PUBKEY_BYTES - size_of::<ReadBytesType>();
const _: () = assert!(MAX_BYTE_OFFSET == 28);
```

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
