### Title
Gateway Stateless Validator Uses Hardcoded `min_gas_price` Config Decoupled from Version-Gated `VersionedConstants.min_gas_price`, Causing Admission/Rejection Mismatch — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `StatelessTransactionValidator` gates incoming transactions using `StatelessTransactionValidatorConfig.min_gas_price` (hardcoded default: `8_000_000_000` FRI). The actual protocol-level minimum L2 gas price is defined in `apollo_versioned_constants::VersionedConstants.min_gas_price`, which is version-gated and updated per Starknet version. These two values are entirely independent. When they diverge, the gateway either admits transactions that will be rejected by the execution layer, or rejects transactions that are valid under the current protocol version. The codebase itself acknowledges this with an explicit TODO.

### Finding Description

`StatelessTransactionValidatorConfig` declares a standalone `min_gas_price: u128` field with a hardcoded default of `8_000_000_000`: [1](#0-0) 

The stateless validator enforces this threshold at gateway admission: [2](#0-1) 

Separately, `apollo_versioned_constants::VersionedConstants` carries its own `min_gas_price: GasPrice` that is version-gated across Starknet versions V0_14_0 through V0_14_4: [3](#0-2) 

The fee market uses `VersionedConstants::latest_constants().min_gas_price` as the authoritative floor when computing the next block's L2 gas price: [4](#0-3) 

The codebase explicitly acknowledges the divergence with a TODO: [5](#0-4) 

The two values are never reconciled. The gateway config value is a static deployment artifact; the versioned-constants value is updated per Starknet protocol version. Any version upgrade that changes `VersionedConstants.min_gas_price` leaves the gateway's admission threshold stale.

### Impact Explanation

**Case A — `VersionedConstants.min_gas_price` > `config.min_gas_price` (8 Gwei):**
Transactions with `l2_gas.max_price_per_unit` in the range `[config_min, vc_min)` pass the stateless validator and enter the mempool. The block's L2 gas price is floored at `vc_min`, so these transactions will fail the stateful validator's check against the previous block's L2 gas price. The gateway admits transactions that are invalid under the current protocol version.

**Case B — `VersionedConstants.min_gas_price` < `config.min_gas_price` (8 Gwei):**
Transactions with `l2_gas.max_price_per_unit` in the range `[vc_min, config_min)` are rejected by the stateless validator even though they satisfy the protocol's actual minimum. The gateway rejects valid transactions before sequencing.

Both cases match the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."*

### Likelihood Explanation

**Medium.** The divergence is structural and permanent until the TODO is resolved. Any Starknet version upgrade that modifies `VersionedConstants.min_gas_price` without a corresponding update to the deployed gateway config triggers the mismatch. The diff-regression file `0.14.0_0.14.1.txt` already records a change to `min_gas_price` between versions, confirming the value does change across versions:



### Recommendation

Remove `min_gas_price` from `StatelessTransactionValidatorConfig` and replace the admission check with a direct read from `VersionedConstants::latest_constants().min_gas_price`, exactly as the TODO instructs. This ensures the gateway's admission threshold is always in sync with the version-gated protocol constant used by the fee market and block production layer.

### Proof of Concept

1. Deploy the sequencer with the default config (`StatelessTransactionValidatorConfig.min_gas_price = 8_000_000_000`).
2. Upgrade to a Starknet version where `VersionedConstants.min_gas_price` is, say, `10_000_000_000` FRI.
3. Submit a V3 `InvokeTransaction` with `l2_gas.max_price_per_unit = 9_000_000_000` FRI.
4. The stateless validator passes: `9_000_000_000 >= 8_000_000_000` ✓
5. The block's L2 gas price is floored at `10_000_000_000` by the fee market.
6. The stateful validator rejects the transaction: `9_000_000_000 < 10_000_000_000` ✗
7. The gateway has admitted a transaction that is invalid under the current protocol version, wasting mempool capacity and producing a misleading acceptance signal to the user.

Conversely, if `VersionedConstants.min_gas_price` drops below `8_000_000_000` in a future version, transactions with gas prices above the protocol floor but below the stale config value are silently rejected at the gateway, denying service to users submitting otherwise-valid transactions.

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L170-192)
```rust
    // TODO(AlonH): Remove the `min_gas_price` field from this struct and use the one from the
    // versioned constants.
    pub min_gas_price: u128,
    pub max_l2_gas_amount: u64,
    pub max_calldata_length: usize,
    pub max_signature_length: usize,
    pub max_proof_size: usize,

    // Declare txs specific config.
    pub max_contract_bytecode_size: usize,
    pub max_contract_class_object_size: usize,
    pub min_sierra_version: VersionId,
    pub max_sierra_version: VersionId,

    // If true, allows transactions with non-empty proof_facts or proof fields.
    pub allow_client_side_proving: bool,
}

impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L71-76)
```rust
        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }
```

**File:** crates/apollo_versioned_constants/src/lib.rs (L15-43)
```rust
    /// The minimum gas price in fri.
    pub min_gas_price: GasPrice,
    /// The maximum block size in gas units.
    // NOTE: Must stay in sync with BouncerWeights receipt_l2_gas.
    // NOTE: When max_block_size is changed, update `gas_target` accordingly to maintain the ratio.
    pub max_block_size: GasAmount,
    /// The target gas usage per block. Used by EIP-1559 to calculate the next block's gas price.
    // Target is 60% of max_block_size, making price adjustment more responsive to congestion.
    pub gas_target: GasAmount,
    /// The margin for the eth to fri rate disagreement, expressed as a percentage (parts per
    /// hundred).
    pub l1_gas_price_margin_percent: u32,
    /// Number of `fee_proposal` values used to compute `fee_actual` (sliding window).
    pub fee_proposal_window_size: u64,
    /// Maximum `fee_proposal` change per block in parts per thousand (e.g., `2` = 0.2%).
    pub fee_proposal_margin_ppt: u128,
}

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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L41-52)
```rust
pub fn get_min_gas_price_for_height(
    height: BlockNumber,
    min_l2_gas_price_per_height: &[PricePerHeight],
) -> GasPrice {
    let fallback_min_gas_price = VersionedConstants::latest_constants().min_gas_price;
    min_l2_gas_price_per_height
        .iter()
        .rev()
        .find(|e| e.height <= height.0)
        .map(|e| GasPrice(e.price))
        .unwrap_or(fallback_min_gas_price)
}
```
