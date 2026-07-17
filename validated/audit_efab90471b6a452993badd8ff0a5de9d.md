### Title
Stale `shard_layout` and `last_resharding` Stored in Fallback `EpochInfo` When `proposals_to_epoch_info` Fails During a Dynamic Resharding Epoch Boundary — (`File: chain/epoch-manager/src/lib.rs`)

---

### Summary

In `EpochManager::finalize_epoch()`, when `proposals_to_epoch_info()` fails with `EpochError::ThresholdError` or `EpochError::NotEnoughValidators`, the fallback path clones the **previous** epoch's `EpochInfo` (`next_epoch_info`, i.e., epoch T+1) and only increments `epoch_height`. It does **not** update the `shard_layout`, `last_resharding`, `chunk_producers_settlement`, or `protocol_version` fields. When `ProtocolFeature::DynamicResharding` is active and a shard split has been scheduled for epoch T+2, this stores a permanently stale `EpochInfoV5` for epoch T+2 — one whose `shard_layout` reflects the old N-shard layout instead of the newly derived N+1-shard layout. Every subsequent call to `get_shard_layout()` for epoch T+2 returns the wrong layout, breaking shard routing, chunk producer assignment, and the resharding cooldown invariant.

---

### Finding Description

`finalize_epoch()` computes the correct `next_next_shard_layout` (the derived V3 layout for epoch T+2) and the correct `last_resharding` value before calling `proposals_to_epoch_info()`:

```rust
// chain/epoch-manager/src/lib.rs:918-929
let next_next_shard_layout = self.next_next_shard_layout(...)?;

let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
let last_resharding = (!has_same_shard_layout)
    .then(|| next_epoch_info.epoch_height() + 1)
    .or_else(|| next_epoch_info.last_resharding());
```

These values are passed into `proposals_to_epoch_info()` (lines 947, 949), which, on success, constructs a fresh `EpochInfoV5` with the correct `shard_layout` and `last_resharding`.

However, on failure, the fallback path is:

```rust
// chain/epoch-manager/src/lib.rs:952-963
Err(EpochError::ThresholdError { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);  // clones epoch T+1
    *epoch_info.epoch_height_mut() += 1;
    epoch_info
}
Err(EpochError::NotEnoughValidators { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);  // clones epoch T+1
    *epoch_info.epoch_height_mut() += 1;
    epoch_info
}
```

The cloned `EpochInfo` retains epoch T+1's `shard_layout` (N shards), `last_resharding`, `chunk_producers_settlement` (N entries), and `protocol_version`. None of the fields computed for epoch T+2 are applied. This stale `EpochInfoV5` is then persisted via `save_epoch_info()` at line 977.

The `EpochInfo::shard_layout()` accessor returns `Some(&v5.shard_layout)` only for `V5`, and `get_shard_layout()` trusts this value as the authoritative source for dynamic resharding epochs:

```rust
// chain/epoch-manager/src/lib.rs:1759-1772
pub fn get_shard_layout(&self, epoch_id: &EpochId) -> Result<ShardLayout, EpochError> {
    let epoch_info = self.get_epoch_info(epoch_id)?;
    if let Some(shard_layout) = epoch_info.shard_layout() {
        Ok(shard_layout.clone())   // returns stale N-shard layout
    } else { ... }
}
```

The `can_reshard()` cooldown check also reads `last_resharding` from the stored `EpochInfo`:

```rust
// chain/epoch-manager/src/lib.rs:845-847
let can_reshard = next_epoch_info.last_resharding().is_none_or(|last_resharding| {
    next_epoch_info.epoch_height() - last_resharding >= min_epochs_between_resharding.get()
});
```

With a stale `last_resharding`, the cooldown is computed against the wrong epoch height, potentially allowing back-to-back reshardings, which the documentation explicitly marks as unsafe.

---

### Impact Explanation

**High.** When the fallback path fires during a dynamic resharding epoch boundary:

1. **Wrong shard layout for epoch T+2**: `get_shard_layout(&epoch_T2_id)` returns the old N-shard layout. All shard routing (`account_id_to_shard_id`), chunk producer assignment, and state management for epoch T+2 use the wrong layout. Nodes that correctly derive the N+1-shard layout from the block header's `shard_split` field will disagree with the stored `EpochInfo`, causing a consensus split.

2. **Wrong `chunk_producers_settlement`**: The cloned settlement has N entries (one per old shard). Epoch T+2 needs N+1 entries. Calls to `sample_chunk_producer()` for the new shard index will return `None` (out-of-bounds), breaking chunk production for the new shard.

3. **Broken resharding cooldown**: The stale `last_resharding` causes `can_reshard()` to compute the wrong cooldown, potentially allowing a second split in the immediately following epoch, which the code explicitly documents as unsafe (it would cause `InvalidChunkHeaderShardSplit`).

4. **Wrong `protocol_version`**: The fallback epoch info carries epoch T+1's protocol version instead of `next_next_epoch_version`, causing `RuntimeConfigStore::get_config()` to return the wrong fee/gas parameters for epoch T+2.

---

### Likelihood Explanation

**Medium.** The trigger requires two simultaneous conditions:

1. `ProtocolFeature::DynamicResharding` is active (protocol version 153+) and a shard split has been scheduled for epoch T+2 (i.e., `next_next_shard_layout != next_shard_layout`).

2. `proposals_to_epoch_info()` returns `NotEnoughValidators` or `ThresholdError`. The `NotEnoughValidators` path is particularly realistic during a resharding event: the new layout has N+1 shards, and if `num_chunk_producers < minimum_validators_per_shard * (N+1)`, the error fires. This is a natural protocol-state condition that does not require any privileged action — it follows from the validator set size relative to the new shard count. Any validator can unstake to push the count below the threshold.

---

### Recommendation

In both fallback arms, after cloning `next_epoch_info`, apply the epoch-T+2-specific fields that were already computed:

```rust
Err(EpochError::ThresholdError { .. }) | Err(EpochError::NotEnoughValidators { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    // Apply the fields computed for epoch T+2:
    if let EpochInfo::V5(ref mut v5) = epoch_info {
        v5.shard_layout = next_next_shard_layout.clone();
        v5.last_resharding = last_resharding;
        v5.protocol_version = next_next_epoch_version;
        // chunk_producers_settlement must also be recomputed or zeroed
        // to match the new shard count.
    }
    epoch_info
}
```

A cleaner fix is to construct a minimal but correct `EpochInfoV5` for the fallback case rather than cloning the previous epoch's info.

---

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` (protocol version ≥ 153).
2. Configure `force_split_shards` to force a split of shard S at epoch boundary T → T+2.
3. At epoch T's last block, ensure the validator set is small enough that `num_chunk_producers < minimum_validators_per_shard * (N+1)` (e.g., have validators unstake to drop below the threshold for the new N+1-shard layout).
4. `finalize_epoch()` computes `next_next_shard_layout` as the derived V3 layout (N+1 shards) and calls `proposals_to_epoch_info()` with it.
5. `proposals_to_epoch_info()` → `get_chunk_producers_assignment()` → `assign_chunk_producers_to_shards()` returns `NotEnoughValidators` because there are fewer chunk producers than `minimum_validators_per_shard * (N+1)`.
6. The fallback at lines 958–962 clones `next_epoch_info` (N-shard layout) and stores it as epoch T+2's `EpochInfo`.
7. All subsequent calls to `get_shard_layout(&epoch_T2_id)` return the N-shard layout. Nodes that read the block header's `shard_split` field and derive the N+1-shard layout will disagree, causing a consensus failure.

**Exact divergent value**: `EpochInfoV5.shard_layout` for epoch T+2 is `ShardLayoutV2/V3` with N boundary accounts instead of the correct `ShardLayoutV3` with N+1 boundary accounts (the new split boundary is absent). `EpochInfoV5.last_resharding` is `None` or the stale T+1 value instead of `Some(epoch_T1_height + 1)`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L843-848)
```rust
        // has been enabled. It is theoretically possible that a static resharding was scheduled
        // right before enabling dynamic resharding, but we assume this didn't happen.
        let can_reshard = next_epoch_info.last_resharding().is_none_or(|last_resharding| {
            next_epoch_info.epoch_height() - last_resharding >= min_epochs_between_resharding.get()
        });
        Ok(can_reshard)
```

**File:** chain/epoch-manager/src/lib.rs (L926-929)
```rust
        let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
        let last_resharding = (!has_same_shard_layout)
            .then(|| next_epoch_info.epoch_height() + 1)
            .or_else(|| next_epoch_info.last_resharding());
```

**File:** chain/epoch-manager/src/lib.rs (L938-963)
```rust
        let next_next_epoch_info = match proposals_to_epoch_info(
            &next_next_epoch_config,
            rng_seed,
            &next_epoch_info,
            all_proposals,
            validator_kickout,
            validator_reward,
            minted_amount,
            next_next_epoch_version,
            next_next_shard_layout.clone(),
            &strategy,
            last_resharding,
        ) {
            Ok(next_next_epoch_info) => next_next_epoch_info,
            Err(EpochError::ThresholdError { stake_sum, num_seats }) => {
                tracing::warn!(target: "epoch_manager", %stake_sum, %num_seats, "not enough stake for required number of seats (all validators tried to unstake?)");
                let mut epoch_info = EpochInfo::clone(&next_epoch_info);
                *epoch_info.epoch_height_mut() += 1;
                epoch_info
            }
            Err(EpochError::NotEnoughValidators { num_validators, num_shards }) => {
                tracing::warn!(target: "epoch_manager", %num_validators, %num_shards, "not enough validators for required number of shards (all validators tried to unstake?)");
                let mut epoch_info = EpochInfo::clone(&next_epoch_info);
                *epoch_info.epoch_height_mut() += 1;
                epoch_info
            }
```

**File:** chain/epoch-manager/src/lib.rs (L1759-1772)
```rust
    pub fn get_shard_layout(&self, epoch_id: &EpochId) -> Result<ShardLayout, EpochError> {
        let epoch_info = self.get_epoch_info(epoch_id)?;
        if let Some(shard_layout) = epoch_info.shard_layout() {
            Ok(shard_layout.clone())
        } else {
            let protocol_version = epoch_info.protocol_version();
            self.get_static_shard_layout_for_protocol_version(protocol_version).ok_or_else(|| {
                EpochError::ShardingError(format!(
                    "shard layout missing. epoch_id={:?} protocol_version={}",
                    epoch_id, protocol_version
                ))
            })
        }
    }
```

**File:** core/primitives/src/epoch_info.rs (L45-71)
```rust
// V4 -> V5: Add shard layout (for dynamic resharding)
#[derive(
    BorshSerialize, BorshDeserialize, Clone, Debug, PartialEq, Eq, serde::Serialize, ProtocolSchema,
)]
pub struct EpochInfoV5 {
    pub epoch_height: EpochHeight,
    pub validators: Vec<ValidatorStake>,
    pub validator_to_index: HashMap<AccountId, ValidatorId>,
    pub block_producers_settlement: Vec<ValidatorId>,
    pub chunk_producers_settlement: Vec<Vec<ValidatorId>>,
    pub stake_change: BTreeMap<AccountId, Balance>,
    pub validator_reward: HashMap<AccountId, Balance>,
    pub validator_kickout: HashMap<AccountId, ValidatorKickoutReason>,
    pub minted_amount: Balance,
    pub seat_price: Balance,
    pub protocol_version: ProtocolVersion,
    pub shard_layout: ShardLayout,
    /// The epoch height at which the most recent resharding occurred.
    /// `None` means no resharding has happened since dynamic resharding was enabled.
    pub last_resharding: Option<EpochHeight>,
    // stuff for selecting validators at each height
    rng_seed: RngSeed,
    block_producers_sampler: StakeWeightedIndex,
    chunk_producers_sampler: Vec<StakeWeightedIndex>,
    /// Contains the epoch's validator mandates. Used to sample chunk validators.
    validator_mandates: ValidatorMandates,
}
```

**File:** core/primitives/src/epoch_info.rs (L697-711)
```rust
    pub fn shard_layout(&self) -> Option<&ShardLayout> {
        match self {
            Self::V5(v5) => Some(&v5.shard_layout),
            _ => None,
        }
    }

    /// Get the epoch height at which the most recent resharding occurred.
    /// Returns `None` for pre-V5 `EpochInfo` or when no resharding has happened.
    pub fn last_resharding(&self) -> Option<EpochHeight> {
        match self {
            Self::V1(_) | Self::V2(_) | Self::V3(_) | Self::V4(_) => None,
            Self::V5(v5) => v5.last_resharding,
        }
    }
```
