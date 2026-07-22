### Title
Stale `min_gas_price` in `StatelessTransactionValidatorConfig` Diverges from Canonical `VersionedConstants` — (File: `crates/apollo_gateway_config/src/config.rs`)

---

### Summary

`StatelessTransactionValidator` enforces a locally-stored `min_gas_price` from `StatelessTransactionValidatorConfig` instead of reading it from the canonical `VersionedConstants`. A developer TODO comment in the source explicitly acknowledges this divergence. When the protocol version advances and `VersionedConstants.min_gas_price` changes, the gateway continues to apply the stale static value, causing incorrect admission decisions: transactions with gas prices below the new canonical minimum are accepted, or transactions above the stale-but-now-too-high threshold are wrongly rejected.

---

### Finding Description

`StatelessTransactionValidatorConfig` stores `min_gas_price` as a plain `u128` field: [1](#0-0) 

The field's own comment acknowledges it should not exist here:

```rust
// TODO(AlonH): Remove the `min_gas_price` field from this struct and use the one from the
// versioned constants.
pub min_gas_price: u128,
``` [1](#0-0) 

`StatelessTransactionValidator::validate_resource_bounds` uses this stored value directly to gate admission:

```rust
if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
}
``` [2](#0-1) 

The canonical source is `apollo_versioned_constants::VersionedConstants`, which carries a per-protocol-version `min_gas_price: GasPrice` field: [3](#0-2) 

`VersionedConstants` is versioned across Starknet releases (V0_14_0 through V0_14_4): [4](#0-3) 

The default value baked into `StatelessTransactionValidatorConfig` is `8_000_000_000`: [5](#0-4) 

This value is set once at node startup and never re-read from `VersionedConstants`. The three stale-value scenarios from the original report map directly:

1. **Default never updated** — if the operator never overrides `min_gas_price` in the gateway config, the hardcoded default diverges from whatever `VersionedConstants` specifies for the running protocol version.
2. **Protocol version upgraded** — a software upgrade that bumps `VersionedConstants.min_gas_price` (e.g., to raise the floor) leaves the gateway config unchanged; the gateway continues accepting transactions at the old, lower floor.
3. **Config updated independently** — if the gateway config is updated to a value that does not match the current `VersionedConstants`, the gateway and the execution/consensus layer apply different floors.

---

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions or rejects valid transactions before sequencing.**

- If `VersionedConstants.min_gas_price` rises above the stale config value, the gateway admits transactions whose offered gas price is below the canonical protocol minimum. These transactions enter the mempool and may be sequenced into blocks, where they can cause incorrect fee accounting or be rejected by the stateful validator after consuming gateway resources.
- If `VersionedConstants.min_gas_price` falls below the stale config value, the gateway rejects transactions that the protocol would accept, causing valid user transactions to be silently dropped at the RPC layer.

---

### Likelihood Explanation

**Low.** The divergence requires a software upgrade that changes `VersionedConstants.min_gas_price` without a corresponding update to the gateway static config, or an operator misconfiguration. The TODO comment confirms this is a known, unresolved code-level issue rather than a purely operational one.

---

### Recommendation

Remove `min_gas_price` from `StatelessTransactionValidatorConfig` (as the TODO already prescribes) and replace the admission check with a live read from the `VersionedConstants` instance appropriate for the current protocol version. The `StatelessTransactionValidator` should accept a reference to `VersionedConstants` (or at minimum its `min_gas_price` field) at validation time rather than caching it at construction time.

---

### Proof of Concept

1. Deploy the sequencer with the default `StatelessTransactionValidatorConfig` (`min_gas_price = 8_000_000_000`).
2. Upgrade the software to a version whose `VersionedConstants` (e.g., `orchestrator_versioned_constants_0_14_X.json`) sets `min_gas_price` to `16_000_000_000`.
3. Submit an `InvokeV3` transaction with `l2_gas.max_price_per_unit = 10_000_000_000` (above the stale config floor, below the canonical VC floor).
4. `StatelessTransactionValidator::validate_resource_bounds` reads `self.config.min_gas_price = 8_000_000_000`, passes the check, and forwards the transaction to the mempool.
5. The transaction carries a gas price that violates the canonical protocol minimum, producing an incorrect admission decision at the gateway boundary. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L167-204)
```rust
pub struct StatelessTransactionValidatorConfig {
    // If true, ensures that at least one resource bound (L1, L2, or L1 data) is greater than zero.
    pub validate_resource_bounds: bool,
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
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

**File:** crates/apollo_versioned_constants/src/lib.rs (L15-16)
```rust
    /// The minimum gas price in fri.
    pub min_gas_price: GasPrice,
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
