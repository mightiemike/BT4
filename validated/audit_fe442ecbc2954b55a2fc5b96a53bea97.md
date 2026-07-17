### Title
`get_sortable_validator_online_ratio` ignores `endorsement_cutoff_threshold`, causing divergent exemption-sort and reward/kickout metrics — (File: `chain/epoch-manager/src/validator_stats.rs`)

---

### Summary

`get_sortable_validator_online_ratio` always calls `get_validator_online_ratio(stats, None)`, hardcoding the `endorsement_cutoff_threshold` to `None`. The actual reward and kickout-exemption logic passes the real `endorsement_cutoff_threshold` from the epoch config. When the threshold is active, the sort used to build the kickout-exemption list ranks validators by a different metric than the one used to decide their reward and kickout outcome, producing an inconsistent validator-set selection at every epoch boundary.

---

### Finding Description

`get_validator_online_ratio` accepts an `endorsement_cutoff_threshold: Option<u8>`. When `Some(t)` is supplied, the endorsement component of the online ratio is binarized: it becomes `0/1` if the validator's endorsement ratio is below `t`, or `1/1` if at or above it. This binarized value is what `calculate_reward` uses to determine whether a validator's average uptime crosses `online_min_threshold` and to compute the reward multiplier. [1](#0-0) 

The kickout-exemption path in `compute_validators_to_reward_and_kickout` sorts all validators by online ratio (highest first) and exempts the top-stake slice from kickout. That sort is produced by `get_sortable_validator_online_ratio`: [2](#0-1) 

Line 111 unconditionally passes `None`:

```rust
let ratio = get_validator_online_ratio(stats, None);
```

The sort is then consumed by `compute_exempted_kickout`: [3](#0-2) 

Meanwhile, `calculate_reward` passes the real threshold: [4](#0-3) 

The two code paths therefore compute different online ratios for the same validator whenever `endorsement_cutoff_threshold` is `Some(t)`.

**Concrete divergence.** Suppose `endorsement_cutoff_threshold = Some(50)` and `online_min_threshold = 0.9`.

| Validator | blocks | chunks | endorsements | Sort ratio (no cutoff) | Reward ratio (cutoff=50) |
|-----------|--------|--------|--------------|------------------------|--------------------------|
| A | 95/100 | 95/100 | 49/100 | (0.95+0.95+0.49)/3 ≈ **0.797** | (0.95+0.95+0)/3 ≈ **0.633** |
| B | 70/100 | 70/100 | 70/100 | (0.70+0.70+0.70)/3 = **0.700** | (0.70+0.70+1)/3 ≈ **0.800** |

The sort ranks A above B (0.797 > 0.700), so A is exempted from kickout first. But the reward metric ranks B above A (0.800 > 0.633). A validator like A — whose endorsement ratio sits just below the cutoff — appears artificially elevated in the exemption sort relative to its true protocol-level uptime. A validator like B, whose endorsement ratio clears the cutoff, is ranked lower in the sort than its true uptime warrants and may be kicked out while A is protected.

---

### Impact Explanation

**High.** The exemption list directly controls which validators survive the epoch boundary. When `endorsement_cutoff_threshold` is active, validators with endorsement ratios just below the cutoff are sorted as if their endorsement contribution is real (e.g., 49%), while the protocol treats it as zero for reward and effective-uptime purposes. This allows such validators to occupy exemption slots that should go to validators with genuinely higher protocol-level uptime, corrupting the validator set. The wrong set propagates into `EpochInfo` for epoch N+2, affecting block/chunk producer assignments and staking rewards for the entire subsequent epoch.

---

### Likelihood Explanation

**Low.** The divergence is only reachable when `endorsement_cutoff_threshold` is set to `Some(t)` in the production epoch config and when at least one validator's endorsement ratio falls in the range `[0, t)` while its block/chunk production is high enough to push its raw online ratio above that of validators whose endorsement ratio clears the cutoff. Both conditions must coincide at an epoch boundary.

---

### Recommendation

Pass the active `endorsement_cutoff_threshold` into `get_sortable_validator_online_ratio` so the sort metric is identical to the reward/kickout metric:

```rust
// in compute_validators_to_reward_and_kickout, replace:
.map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
// with:
.map(|(account, stats)| (
    get_sortable_validator_online_ratio(stats, config.endorsement_cutoff_threshold),
    account
))
```

And update `get_sortable_validator_online_ratio` to accept and forward the threshold to `get_validator_online_ratio`.

---

### Proof of Concept

1. Deploy a network with `endorsement_cutoff_threshold = Some(50)`, `online_min_threshold = 9/10`, `validator_max_kickout_stake_perc = 70` (so `exempt_perc = 30`).
2. Configure two validators with equal stake:
   - **V_A**: produces 95% of blocks, 95% of chunks, 49% of endorsements.
   - **V_B**: produces 70% of blocks, 70% of chunks, 70% of endorsements.
3. At epoch end, `get_sortable_validator_online_ratio` ranks V_A at ≈0.797 and V_B at 0.700. V_A is placed in the exempted set.
4. `calculate_reward` computes V_A's effective uptime as ≈0.633 (below `online_min_threshold = 0.9`), so V_A receives zero reward and should be a kickout candidate.
5. Because V_A is in the exempted set, it is not kicked out. V_B, with a true uptime of ≈0.800, may be kicked out instead.
6. The resulting `EpochInfo` for epoch N+2 retains V_A (an underperforming validator) and ejects V_B (a better-performing one). [2](#0-1) [5](#0-4) [4](#0-3)

### Citations

**File:** chain/epoch-manager/src/validator_stats.rs (L16-24)
```rust
pub(crate) fn get_validator_online_ratio(
    stats: &BlockChunkValidatorStats,
    endorsement_cutoff_threshold: Option<u8>,
) -> Ratio<U256> {
    let expected_blocks = stats.block_stats.expected;
    let expected_chunks = stats.chunk_stats.expected();

    let (produced_endorsements, expected_endorsements) =
        get_endorsement_ratio(stats.chunk_stats.endorsement_stats(), endorsement_cutoff_threshold);
```

**File:** chain/epoch-manager/src/validator_stats.rs (L110-118)
```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);
    let mut bytes: [u8; size_of::<U256>()] = [0; size_of::<U256>()];
    ratio.numer().to_little_endian(&mut bytes);
    let bignumer = BigUint::from_bytes_le(&bytes);
    ratio.denom().to_little_endian(&mut bytes);
    let bigdenom = BigUint::from_bytes_le(&bytes);
    BigRational::new(bignumer.try_into().unwrap(), bigdenom.try_into().unwrap())
}
```

**File:** chain/epoch-manager/src/lib.rs (L500-516)
```rust
        let mut sorted_validators = validator_block_chunk_stats
            .iter()
            .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
            .collect_vec();
        sorted_validators.sort_by(validator_comparator);
        let accounts_sorted_by_online_ratio =
            sorted_validators.into_iter().map(|(_, account)| account.clone()).collect_vec();

        let exempt_perc =
            100_u8.checked_sub(config.validator_max_kickout_stake_perc).unwrap_or_default();
        let exempted_validators = Self::compute_exempted_kickout(
            epoch_info,
            &accounts_sorted_by_online_ratio,
            total_stake,
            exempt_perc,
            prev_validator_kickout,
        );
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L94-98)
```rust
        for (account_id, stats) in validator_block_chunk_stats {
            let production_ratio =
                get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
            let average_produced_numer = production_ratio.numer();
            let average_produced_denom = production_ratio.denom();
```
