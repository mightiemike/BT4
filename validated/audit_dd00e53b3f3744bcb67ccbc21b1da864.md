### Title
`EarlyKickout` Blacklist Computed But Never Consulted in `seed_chunk_producers` Assignment Path — (`chain/epoch-manager/src/lib.rs`, `chain/epoch-manager/src/adapter.rs`)

### Summary

The `ProtocolFeature::EarlyKickout` feature (nightly protocol version 152+) computes a per-shard chunk-producer blacklist via `compute_chunk_producer_blacklist` / `get_chunk_producer_blacklist`, but this blacklist has **zero production callers** and is never consulted when seeding chunk-producer assignments in `seed_chunk_producers`. As a result, chunk producers that the protocol has identified as underperforming are still assigned to produce chunks, and their chunks are accepted by the network — the early-kickout mechanism is a dead letter.

---

### Finding Description

When `EarlyKickout` is enabled, the epoch manager executes two independent, non-communicating paths:

**Path A — blacklist computation (never enforced)**

`compute_chunk_producer_blacklist` marks a validator as blacklisted on a shard when, within the current epoch:
- `expected >= 50` (sample-size guard)
- `missed >= 20`
- `produced * 100 < expected * 80` (production ratio < 80%) [1](#0-0) 

`get_chunk_producer_blacklist` wraps this computation behind the `EarlyKickout` protocol gate and returns the per-shard blacklist. [2](#0-1) 

The test file for this feature explicitly documents the gap:

> "The math is pure with **no production callers**; these tests exercise the math directly and the accessor end-to-end." [3](#0-2) 

**Path B — chunk-producer seeding (ignores blacklist)**

`seed_chunk_producers` writes the canonical sampler result — `sample_chunk_producer`, with no exclusion set — into `DBCol::ChunkProducers` for every shard:

```rust
if let Some(validator_id) =
    sample_epoch_info.sample_chunk_producer(sample_shard_layout, shard_id, height)
{
    let validator_stake = sample_epoch_info.get_validator(validator_id);
    store_update.set_chunk_producer(block_hash, shard_id, &validator_stake);
}
``` [4](#0-3) 

`get_chunk_producer_info_anchored` then reads this stored value for all chunk-producer verification: [5](#0-4) 

The function `sample_chunk_producer_excluding` exists precisely to filter out a blacklist set, and is tested: [6](#0-5) 

But it is **never called** from `seed_chunk_producers`. The blacklist computed in Path A is never passed to Path B.

A TODO comment in the adapter acknowledges the gap:

> "TODO(early-kickout): once dynamic sampling ships and the DB may diverge from computation (blacklisted producers excluded), consider adding a lenient variant with computation fallback for non-critical paths." [7](#0-6) 

---

### Impact Explanation

When `EarlyKickout` activates (nightly protocol version 152), a chunk producer that has crossed the blacklist threshold — missed ≥20 chunks, production ratio <80%, ≥50 expected — is still seeded into `DBCol::ChunkProducers` via canonical sampling. `get_chunk_producer_info_anchored` reads that row and accepts the producer as legitimate. Chunk state witnesses from that producer pass signature verification and are endorsed. The early-kickout mechanism produces a blacklist that is never enforced, so the underperforming producer continues to occupy chunk-production slots for the remainder of the epoch, degrading shard liveness.

---

### Likelihood Explanation

The `EarlyKickout` feature is active on nightly/betanet builds (protocol version 152). Any chunk producer that crosses the statistical threshold during an epoch will be blacklisted by `get_chunk_producer_blacklist` but will continue to be assigned by `seed_chunk_producers`. No adversarial action is required — ordinary liveness failures trigger the condition automatically.

---

### Recommendation

In `seed_chunk_producers`, replace the call to `sample_chunk_producer` with `sample_chunk_producer_excluding`, passing the blacklist computed from the aggregator's stats at the anchor block:

```rust
// Compute blacklist at the anchor
let blacklist = compute_chunk_producer_blacklist(
    &aggregator.shard_tracker,
    sample_epoch_info,
    sample_shard_layout,
);
for shard_id in sample_shard_layout.shard_ids() {
    let exclude = blacklist.get(&shard_id).cloned().unwrap_or_default();
    if let Some(validator_id) = sample_epoch_info
        .sample_chunk_producer_excluding(sample_shard_layout, shard_id, height, &exclude)
        .or_else(|| sample_epoch_info.sample_chunk_producer(sample_shard_layout, shard_id, height))
    {
        ...
    }
}
```

The fallback to `sample_chunk_producer` when `sample_chunk_producer_excluding` returns `None` mirrors the existing safety-valve logic in `compute_chunk_producer_blacklist` (never blacklist every producer on a shard).

---

### Proof of Concept

1. Build nearcore with `--features nightly` (enables `EarlyKickout` at protocol version 152).
2. Run a local network; configure one chunk producer to miss >20% of its assigned slots (≥20 misses, ≥50 expected).
3. Call `get_chunk_producer_blacklist(anchor_hash)` — the underperforming producer appears in the returned map.
4. Inspect `DBCol::ChunkProducers` at the same anchor — the same producer is still stored as the assigned producer for its shard (canonical sampling, no exclusion).
5. Observe that `get_chunk_producer_info_anchored` returns the blacklisted producer as valid, and its chunk state witnesses are endorsed and included in blocks.

The divergent value is the `ValidatorStake` stored in `DBCol::ChunkProducers`: it reflects the canonical sampler result, not the blacklist-excluded result, so the two paths produce different answers about who the legitimate chunk producer is — and the verification path trusts the unfiltered one. [8](#0-7) [9](#0-8)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L66-119)
```rust
const EARLY_KICKOUT_MIN_MISSES: u64 = 20;
const EARLY_KICKOUT_PRODUCTION_THRESHOLD_NUMERATOR: u64 = 80;
const EARLY_KICKOUT_PRODUCTION_THRESHOLD_DENOMINATOR: u64 = 100;
const EARLY_KICKOUT_MINIMUM_OBSERVED_BLOCKS: u64 = 50;

/// Per-shard chunk-producer blacklist from the aggregator's shard_tracker stats.
/// A validator is blacklisted on a shard when, within the current epoch:
///   - expected >= EARLY_KICKOUT_MINIMUM_OBSERVED_BLOCKS  (sample-size guard)
///   - missed   >= EARLY_KICKOUT_MIN_MISSES               (missed = expected - produced)
///   - produced * 100 < expected * 80                     (production ratio < 80%)
/// Safety valve: if blacklisting would remove every distinct producer on a shard,
/// that shard is omitted (caller falls through to default sampling).
pub fn compute_chunk_producer_blacklist(
    shard_tracker: &HashMap<ShardId, HashMap<ValidatorId, ChunkStats>>,
    epoch_info: &EpochInfo,
    shard_layout: &ShardLayout,
) -> HashMap<ShardId, HashSet<ValidatorId>> {
    let mut result = HashMap::new();
    for (shard_id, validators) in shard_tracker {
        let Ok(shard_index) = shard_layout.get_shard_index(*shard_id) else { continue };
        let Some(settlement) = epoch_info.chunk_producers_settlement().get(shard_index) else {
            continue;
        };
        // Distinct chunk PRODUCERS for this shard. shard_tracker also holds
        // endorsement-only entries (chunk validators that never produced); they must
        // not be blacklist candidates and must not skew the safety-valve denominator.
        // (Today expected>=MIN skips them since production.expected==0 — but make it
        // explicit so threshold tuning can't silently reintroduce the bug.)
        let producers: HashSet<ValidatorId> = settlement.iter().copied().collect();
        let mut blacklisted = HashSet::new();
        for (&validator_id, stats) in validators {
            if !producers.contains(&validator_id) {
                continue;
            }
            let (produced, expected) = (stats.produced(), stats.expected());
            if expected < EARLY_KICKOUT_MINIMUM_OBSERVED_BLOCKS {
                continue;
            }
            let missed = expected.saturating_sub(produced);
            // u128 keeps the ratio comparison overflow-proof.
            if missed >= EARLY_KICKOUT_MIN_MISSES
                && (produced as u128) * (EARLY_KICKOUT_PRODUCTION_THRESHOLD_DENOMINATOR as u128)
                    < (expected as u128) * (EARLY_KICKOUT_PRODUCTION_THRESHOLD_NUMERATOR as u128)
            {
                blacklisted.insert(validator_id);
            }
        }
        // Safety valve: never blacklist every distinct producer on a shard. Backstopped
        // by sample_chunk_producer_excluding -> None on full exclusion.
        if !blacklisted.is_empty() && blacklisted.len() < producers.len() {
            result.insert(*shard_id, blacklisted);
        }
    }
    result
```

**File:** chain/epoch-manager/src/lib.rs (L2122-2145)
```rust
    fn seed_chunk_producers(
        &self,
        store_update: &mut EpochStoreUpdateAdapter,
        block_hash: &CryptoHash,
        block_height: BlockHeight,
        own_epoch_info: &EpochInfo,
        sample_epoch_info: &EpochInfo,
        sample_shard_layout: &ShardLayout,
    ) {
        #[cfg(feature = "nightly")]
        {
            if !ProtocolFeature::EarlyKickout.enabled(own_epoch_info.protocol_version()) {
                return;
            }
            let height = block_height + CHUNK_GRANDPARENT_ANCHOR_HEIGHT_OFFSET;
            for shard_id in sample_shard_layout.shard_ids() {
                if let Some(validator_id) =
                    sample_epoch_info.sample_chunk_producer(sample_shard_layout, shard_id, height)
                {
                    let validator_stake = sample_epoch_info.get_validator(validator_id);
                    store_update.set_chunk_producer(block_hash, shard_id, &validator_stake);
                }
            }
        }
```

**File:** chain/epoch-manager/src/adapter.rs (L579-581)
```rust
    // TODO(early-kickout): once dynamic sampling ships and the DB may
    // diverge from computation (blacklisted producers excluded), consider adding
    // a lenient variant with computation fallback for non-critical paths.
```

**File:** chain/epoch-manager/src/adapter.rs (L621-629)
```rust
    /// Returns the per-shard set of chunk producers whose cumulative epoch stats are past the
    /// early-kickout thresholds, computed from the aggregator's stats up to `anchor_hash`.
    /// The epoch is derived from `anchor_hash` via `get_epoch_id_from_prev_block`. Gated by
    /// `ProtocolFeature::EarlyKickout`: empty when the feature is off, and empty at an epoch
    /// boundary (the aggregator's stats belong to the anchor's epoch).
    fn get_chunk_producer_blacklist(
        &self,
        anchor_hash: &CryptoHash,
    ) -> Result<HashMap<ShardId, HashSet<ValidatorId>>, EpochError>;
```

**File:** chain/epoch-manager/src/adapter.rs (L1107-1116)
```rust
                        let epoch_manager = self.read();
                        let key = get_block_shard_id(anchor, shard_id);
                        return match epoch_manager
                            .store
                            .store_ref()
                            .get_ser::<ValidatorStake>(DBCol::ChunkProducers, &key)
                        {
                            Some(validator) => Ok(validator),
                            None => Err(EpochError::ChunkProducerNotInDB(*anchor, shard_id)),
                        };
```

**File:** chain/epoch-manager/src/adapter.rs (L1132-1155)
```rust
    fn get_chunk_producer_blacklist(
        &self,
        anchor_hash: &CryptoHash,
    ) -> Result<HashMap<ShardId, HashSet<ValidatorId>>, EpochError> {
        let epoch_id = self.get_epoch_id_from_prev_block(anchor_hash)?;
        let protocol_version = self.get_epoch_protocol_version(&epoch_id)?;
        if !ProtocolFeature::EarlyKickout.enabled(protocol_version) {
            return Ok(HashMap::new());
        }
        let epoch_manager = self.read();
        let aggregator = epoch_manager.get_epoch_info_aggregator_upto_last(anchor_hash)?;
        // Aggregator belongs to the anchor's epoch. If the next block starts a new epoch,
        // stats reset -> empty blacklist (boundary reset).
        if aggregator.epoch_id != epoch_id {
            return Ok(HashMap::new());
        }
        let epoch_info = epoch_manager.get_epoch_info(&epoch_id)?;
        let shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;
        Ok(crate::compute_chunk_producer_blacklist(
            &aggregator.shard_tracker,
            epoch_info.as_ref(),
            &shard_layout,
        ))
    }
```

**File:** chain/epoch-manager/src/tests/early_kickout.rs (L1-4)
```rust
//! Tests for the early-kickout blacklist math (`compute_chunk_producer_blacklist`)
//! and the gated `get_chunk_producer_blacklist` accessor. The math is pure with no
//! production callers; these tests exercise the math directly and the accessor
//! end-to-end (gate + boundary reset + enabled path).
```

**File:** core/primitives/src/epoch_info.rs (L600-641)
```rust
    pub fn sample_chunk_producer_excluding(
        &self,
        shard_layout: &ShardLayout,
        shard_id: ShardId,
        height: BlockHeight,
        exclude: &HashSet<ValidatorId>,
    ) -> Option<ValidatorId> {
        if exclude.is_empty() {
            return self.sample_chunk_producer(shard_layout, shard_id, height);
        }
        let shard_index = shard_layout.get_shard_index(shard_id).ok()?;
        match &self {
            Self::V1(v1) => Self::sample_excluding_modulo(
                v1.chunk_producers_settlement.get(shard_index)?,
                exclude,
                height,
            ),
            Self::V2(v2) => Self::sample_excluding_modulo(
                v2.chunk_producers_settlement.get(shard_index)?,
                exclude,
                height,
            ),
            Self::V3(v3) => Self::sample_excluding_weighted(
                v3.chunk_producers_settlement.get(shard_index)?,
                exclude,
                &v3.validators,
                Self::chunk_produce_seed(&v3.rng_seed, height, shard_id),
            ),
            Self::V4(v4) => Self::sample_excluding_weighted(
                v4.chunk_producers_settlement.get(shard_index)?,
                exclude,
                &v4.validators,
                Self::chunk_produce_seed(&v4.rng_seed, height, shard_id),
            ),
            Self::V5(v5) => Self::sample_excluding_weighted(
                v5.chunk_producers_settlement.get(shard_index)?,
                exclude,
                &v5.validators,
                Self::chunk_produce_seed(&v5.rng_seed, height, shard_id),
            ),
        }
    }
```
