### Title
`calculate_next_l2_gas_price_for_fin` and `calculate_next_base_gas_price` read `gas_target` / `max_block_size` from `VersionedConstants::latest_constants()` instead of the block's own `starknet_version` constants, producing a wrong `next_l2_gas_price` committed to the block header — (`File: crates/apollo_consensus_orchestrator/src/fee_market/mod.rs`)

---

### Summary

The EIP-1559 fee-market functions that compute `next_l2_gas_price` always pull `gas_target`, `max_block_size`, and `gas_price_max_change_denominator` from `VersionedConstants::latest_constants()` — the orchestrator constants for the newest Starknet version the binary knows about — rather than from the constants that correspond to the block's own `starknet_version`. Because `gas_target` changed by more than 3× across the shipped versions (3.2 B → 4.0 B → 1.5 B → 1.04 B), using the wrong snapshot can reverse the direction of the EIP-1559 price adjustment, producing an incorrect `next_l2_gas_price` that is stored in the block header and becomes the L2 gas price for every transaction in the following block.

---

### Finding Description

The orchestrator versioned constants (`crates/apollo_versioned_constants`) define per-version fee-market parameters:

| Version | `gas_target` | `max_block_size` |
|---------|-------------|-----------------|
| V0_14_0 | 3,200,000,000 | 4,000,000,000 |
| V0_14_1 | 4,000,000,000 | 5,000,000,000 |
| V0_14_2 | 1,500,000,000 | 5,800,000,000 |
| V0_14_3 | 1,040,000,000 | 5,800,000,000 |
| V0_14_4 | 1,040,000,000 | 5,800,000,000 | [1](#0-0) [2](#0-1) 

`calculate_next_l2_gas_price_for_fin` reads `gas_target` from `latest_constants()` at line 70, not from the block's `starknet_version`: [3](#0-2) 

`calculate_next_base_gas_price` reads `gas_price_max_change_denominator` and `max_block_size` from `latest_constants()` at line 92: [4](#0-3) 

`calculate_next_l2_gas_price` in the consensus context also reads `fee_proposal_window_size` from `latest_constants()`: [5](#0-4) 

`is_proposal_init_valid` reads `l1_gas_price_margin_percent` and `fee_proposal_margin_ppt` from `latest_constants()`: [6](#0-5) [7](#0-6) 

None of these call sites accept a `starknet_version` parameter or call `VersionedConstants::get(block.starknet_version)`. The orchestrator versioned constants struct and its `define_versioned_constants!` macro do expose a `get(version)` accessor: [8](#0-7) 

The `next_l2_gas_price` produced by these functions is stored in `BlockHeaderWithoutHash.next_l2_gas_price` and propagated as the L2 gas price for the next block: [9](#0-8) 

---

### Impact Explanation

**Critical — Incorrect fee / gas accounting with economic impact.**

The EIP-1559 price-adjustment formula is:

```
price_change = price × |gas_used − gas_target| / (gas_target × denominator)
direction: increase if gas_used > gas_target, decrease otherwise
```

When the binary's `latest_constants()` returns V0_14_4 (`gas_target = 1.04 B`) but the block's `starknet_version` is V0_14_0 (`gas_target = 3.2 B`), a block with `gas_used = 2 B` produces:

| Constants used | Comparison | Direction | Magnitude |
|---------------|-----------|-----------|-----------|
| Correct V0_14_0 (3.2 B) | 2 B < 3.2 B | **price decreases** | ≈ 0.78 % |
| Wrong V0_14_4 (1.04 B) | 2 B > 1.04 B | **price increases** | ≈ 1.92 % |

The direction of the EIP-1559 adjustment is reversed. The wrong `next_l2_gas_price` is committed to the block header and becomes the L2 gas price for every transaction in the following block, causing incorrect fee charges, wrong fee estimation results, and potential admission/rejection of transactions based on a price that does not reflect actual network congestion.

---

### Likelihood Explanation

**High.** The `gas_target` changed across every shipped version (3.2 B → 4.0 B → 1.5 B → 1.04 B). Any deployment where the binary is built against a newer version than the network's active `starknet_version` — the normal state during a rolling upgrade or when a node is pre-deployed — triggers the mismatch on every block finalization. Both the proposer and validator call the same `latest_constants()` path, so they agree on the wrong value and the block is committed without any consensus-level rejection.

---

### Recommendation

Pass the block's `starknet_version` into `calculate_next_l2_gas_price_for_fin` and `calculate_next_base_gas_price`, and replace every `VersionedConstants::latest_constants()` call inside those functions with `VersionedConstants::get(starknet_version).expect(...)`. Apply the same fix to `calculate_next_l2_gas_price` in `sequencer_consensus_context.rs` (for `fee_proposal_window_size`) and to the `l1_gas_price_margin_percent` / `fee_proposal_margin_ppt` reads in `is_proposal_init_valid`. The `starknet_version` is already present in `ProposalInit` and is validated before these functions are called, so threading it through requires only signature changes.

---

### Proof of Concept

1. Run a sequencer binary compiled with `StarknetVersion::LATEST = V0_14_4` (so `latest_constants()` returns `gas_target = 1,040,000,000`).
2. Configure the network so `proposal_init_validation.starknet_version = V0_14_0`; the proposer emits `ProposalInit { starknet_version: V0_14_0, … }` and the validator accepts it (version check passes).
3. Build a block with `l2_gas_used = 2,000,000,000`.
4. `calculate_next_l2_gas_price_for_fin` reads `gas_target = 1,040,000,000` from `latest_constants()` and passes it to `calculate_next_base_gas_price`.
5. Inside `calculate_next_base_gas_price`: `gas_used (2 B) > gas_target (1.04 B)` → price **increases** by ≈ 1.92 %.
6. Correct behavior under V0_14_0: `gas_used (2 B) < gas_target (3.2 B)` → price should **decrease** by ≈ 0.78 %.
7. The wrong `next_l2_gas_price` is stored in `BlockHeaderWithoutHash.next_l2_gas_price` and used as the L2 gas price for the next block, causing all fee calculations in that block to use an inflated price derived from an inverted EIP-1559 signal. [10](#0-9) [11](#0-10) [1](#0-0) [12](#0-11)

### Citations

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_0.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 3200000000,
    "max_block_size": 4000000000,
    "min_gas_price": "0xb2d05e00",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_3.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1040000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L55-77)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L86-140)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants = VersionedConstants::latest_constants();
    assert!(
        gas_target < versioned_constants.max_block_size,
        "Gas target must be lower than max block size."
    );
    assert!(gas_target.0 > 0, "Gas target must be greater than zero.");
    assert!(
        versioned_constants.gas_price_max_change_denominator > 0,
        "Denominator constant must be greater than zero."
    );

    // If the current price is below the minimum, apply a gradual adjustment and return early.
    // This allows the price to increase by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block.
    if price < min_gas_price {
        let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
        let adjusted = price.0 + max_increase;
        // Cap at min_gas_price to avoid overshooting
        let adjusted_price = adjusted.min(min_gas_price.0);
        info!(
            "Fee Market: Price {} below minimum gas price {}, adjusted price: {} )",
            price.0, min_gas_price.0, adjusted_price
        );
        return GasPrice(adjusted_price);
    }

    // Use U256 to avoid overflow, as multiplying a u128 by a u64 remains within U256 bounds.
    let gas_delta = U256::from(gas_used.0.abs_diff(gas_target.0));
    let gas_target_u256 = U256::from(gas_target.0);
    let price_u256 = U256::from(price.0);

    // Calculate price change by multiplying first, then dividing. This avoids the precision loss
    // that occurs when dividing before multiplying.
    let denominator =
        gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
    let price_change = (price_u256 * gas_delta) / denominator;

    let adjusted_price_u256 =
        if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };

    // Sanity check: ensure direction of change is correct
    assert!(
        gas_used > gas_target && adjusted_price_u256 >= price_u256
            || gas_used <= gas_target && adjusted_price_u256 <= price_u256
    );

    // Price should not realistically exceed u128::MAX, bound to avoid theoretical overflow.
    let adjusted_price = u128::try_from(adjusted_price_u256).unwrap_or(u128::MAX);
    GasPrice(max(adjusted_price, min_gas_price.0))
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L427-441)
```rust
    fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
        calculate_next_l2_gas_price_for_fin(
            self.l2_gas_price,
            height,
            l2_gas_used,
            self.config.dynamic_config.override_l2_gas_price_fri,
            &self.config.dynamic_config.min_l2_gas_price_per_height,
            fee_actual,
        )
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L329-330)
```rust
    let l1_gas_price_margin_percent =
        VersionedConstants::latest_constants().l1_gas_price_margin_percent.into();
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L401-404)
```rust
        let (lower_bound, upper_bound) = fee_proposal_bounds(
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
```

**File:** crates/apollo_versioned_constants/src/lib.rs (L33-43)
```rust
define_versioned_constants!(
    VersionedConstants,
    VersionedConstantsError,
    StarknetVersion::V0_14_0,
    "resources/versioned_constants_diff_regression",
    (V0_14_0, "../resources/orchestrator_versioned_constants_0_14_0.json"),
    (V0_14_1, "../resources/orchestrator_versioned_constants_0_14_1.json"),
    (V0_14_2, "../resources/orchestrator_versioned_constants_0_14_2.json"),
    (V0_14_3, "../resources/orchestrator_versioned_constants_0_14_3.json"),
    (V0_14_4, "../resources/orchestrator_versioned_constants_0_14_4.json"),
);
```

**File:** crates/starknet_api/src/block.rs (L231-248)
```rust
#[derive(Debug, Default, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct BlockHeaderWithoutHash {
    pub parent_hash: BlockHash,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
    pub state_root: GlobalRoot,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub starknet_version: StarknetVersion,
    // TODO(AndrewL): Add this field into the block hash.
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_4.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1040000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```
