### Title
Missing Cross-Field Validation Allows `min_sierra_version > max_sierra_version` to Permanently Reject All Declare Transactions at the Gateway - (File: crates/apollo_gateway_config/src/config.rs)

### Summary

`StatelessTransactionValidatorConfig` stores `min_sierra_version` and `max_sierra_version` as independent fields with no cross-field invariant check (`min <= max`) at config load time. The admission check in `validate_sierra_version` requires a submitted Sierra version to satisfy both bounds simultaneously. When `min_sierra_version > max_sierra_version`, the acceptance window is empty and every declare transaction is permanently rejected by the gateway, with no recovery path short of restarting the node with a corrected config.

### Finding Description

`StatelessTransactionValidatorConfig` is defined with `#[derive(Validate)]` but carries no cross-field constraint between its two Sierra version bounds:

```rust
// crates/apollo_gateway_config/src/config.rs
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
pub struct StatelessTransactionValidatorConfig {
    ...
    pub min_sierra_version: VersionId,
    pub max_sierra_version: VersionId,
    ...
}
``` [1](#0-0) 

The admission gate in `validate_sierra_version` requires the submitted version to satisfy both bounds at once:

```rust
if self.config.min_sierra_version <= sierra_version && sierra_version <= max_sierra_version {
    return Ok(());
}
Err(StatelessTransactionValidatorError::UnsupportedSierraVersion { ... })
``` [2](#0-1) 

When `min_sierra_version > max_sierra_version`, no value of `sierra_version` can satisfy both inequalities simultaneously. The condition is permanently false, and every declare transaction is rejected with `UnsupportedSierraVersion` regardless of the actual Sierra version it carries.

`validate_node_config` calls `config_validate(self)` which recurses through `#[validate(nested)]` chains, but `StatelessTransactionValidatorConfig` has no cross-field rule, so the inversion is never caught:

```rust
pub fn validate_node_config(&self) -> Result<(), ConfigError> {
    config_validate(self)?;          // only per-field rules fire
    self.cross_member_validations()  // no sierra-version ordering check here
}
``` [3](#0-2) 

The codebase already enforces analogous cross-field ordering constraints elsewhere (e.g., `build_proposal_margin_millis < proposal_timeout`, `idle_delay < batcher_deadline`), but the `min_sierra_version < max_sierra_version` invariant is absent. [4](#0-3) 

### Impact Explanation

With an inverted version window, `StatelessTransactionValidator::validate` returns `UnsupportedSierraVersion` for every declare transaction submitted to the gateway. No declare transaction can ever be admitted to the mempool or sequenced. This matches the allowed impact scope: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

### Likelihood Explanation

The six sub-fields of the two `VersionId` values (`major`, `minor`, `patch` for each) are serialized and loaded independently from the config schema JSON. An operator who bumps `min_sierra_version` to a new major/minor without also updating `max_sierra_version`, or who copies a config fragment in the wrong order, silently produces an inverted window. The default values (`min = 1.1.0`, `max = 1.9.MAX`) are safe, but the absence of a startup check means any misconfiguration goes undetected until declare transactions start failing in production. [5](#0-4) 

### Recommendation

Add a cross-field validation to `StatelessTransactionValidatorConfig` (or to `GatewayStaticConfig`'s nested validation) that asserts `min_sierra_version <= max_sierra_version` at config load time, mirroring the pattern already used for timeout/margin pairs:

```rust
impl StatelessTransactionValidatorConfig {
    fn validate_version_bounds(&self) -> Result<(), ValidationError> {
        if self.min_sierra_version > self.max_sierra_version {
            return Err(create_validation_error(
                format!(
                    "min_sierra_version ({:?}) must be <= max_sierra_version ({:?})",
                    self.min_sierra_version, self.max_sierra_version
                ),
                "invalid_sierra_version_bounds",
                "min_sierra_version must be less than or equal to max_sierra_version",
            ));
        }
        Ok(())
    }
}
```

This should be wired into the `Validate` impl (via `#[validate(custom = "...")]`) or into `validate_node_config`'s `cross_member_validations`, consistent with how `validate_node_dynamic_config` guards the proposal-timeout/margin relationship. [6](#0-5) 

### Proof of Concept

1. Set the gateway config with an inverted Sierra version window, e.g. in `config_schema.json` or a config override:
   ```
   min_sierra_version = { major: 1, minor: 9, patch: 0 }
   max_sierra_version = { major: 1, minor: 1, patch: 0 }
   ```
2. Start the sequencer node. `validate_node_config` succeeds — no error is raised.
3. Submit any declare transaction carrying a Sierra contract at version `1.5.0`.
4. `validate_sierra_version` evaluates `1.9.0 <= 1.5.0` → false; the outer `&&` short-circuits; the function returns `Err(UnsupportedSierraVersion { version: 1.5.0, min: 1.9.0, max: 1.1.0 })`.
5. Every declare transaction is rejected at the gateway. The condition can never become true without a node restart with a corrected config. [2](#0-1)

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L166-186)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
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
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-204)
```rust
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L293-313)
```rust
    fn validate_sierra_version(
        &self,
        sierra_program: &[Felt],
    ) -> StatelessTransactionValidatorResult<()> {
        // Any patch version is valid. (i.e. when check version for upper bound, we ignore the Z
        // part in a version X.Y.Z).
        let mut max_sierra_version = self.config.max_sierra_version;
        max_sierra_version.0.patch = usize::MAX;

        let sierra_version = VersionId::from_sierra_program(sierra_program)?;
        if self.config.min_sierra_version <= sierra_version && sierra_version <= max_sierra_version
        {
            return Ok(());
        }

        Err(StatelessTransactionValidatorError::UnsupportedSierraVersion {
            version: sierra_version,
            min_version: self.config.min_sierra_version,
            max_version: self.config.max_sierra_version,
        })
    }
```

**File:** crates/apollo_node_config/src/node_config.rs (L461-475)
```rust
fn validate_node_dynamic_config(config: &NodeDynamicConfig) -> Result<(), ValidationError> {
    let (Some(consensus), Some(context)) =
        (&config.consensus_dynamic_config, &config.context_dynamic_config)
    else {
        return Ok(());
    };
    let min_timeout = consensus.timeouts.get_proposal_timeout(0);
    let margin = context.build_proposal_margin_millis;
    if margin >= min_timeout {
        return Err(ValidationError::new(
            "build_proposal_margin_millis must be less than the base proposal timeout",
        ));
    }
    Ok(())
}
```

**File:** crates/apollo_node_config/src/node_config.rs (L485-491)
```rust
    pub fn validate_node_config(&self) -> Result<(), ConfigError> {
        // Validate each config member using its `Validate` trait derivation.
        config_validate(self)?;

        // Custom cross member validations.
        self.cross_member_validations()
    }
```

**File:** crates/apollo_config/src/validators.rs (L165-177)
```rust
/// Validates the configured value is positive. If the value is not positive, it returns an
/// error.
pub fn validate_positive(value: usize) -> Result<(), ValidationError> {
    if value > 0 {
        Ok(())
    } else {
        Err(create_validation_error(
            format!("Invalid value: {value}"),
            "Invalid value",
            "Ensure value is positive, i.e., strictly greater than 0.",
        ))
    }
}
```
