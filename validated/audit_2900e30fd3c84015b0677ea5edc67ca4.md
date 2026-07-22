### Title
Gateway `max_l2_gas_amount` admission limit silently bypassed for Declare transactions - (File: crates/apollo_gateway/src/stateless_transaction_validator.rs)

### Summary
The `StatelessTransactionValidator` enforces a `max_l2_gas_amount` ceiling on `l2_gas.max_amount` for Invoke and DeployAccount transactions, but explicitly skips this check for Declare transactions. This creates a direct analog to the external report's discrepancy: the hardcoded admission constant (`max_l2_gas_amount = 1,210,000,000`) is not enforced for one entire transaction type, allowing Declare transactions with arbitrarily large `l2_gas.max_amount` to pass gateway admission.

### Finding Description

In `validate_resource_bounds`, the `max_l2_gas_amount` check is guarded by a Declare-type early-return:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { ... });
}
``` [1](#0-0) 

The default `max_l2_gas_amount` is `1_210_000_000`: [2](#0-1) 

This value is deliberately set to equal `execute_max_sierra_gas (1,110,000,000) + validate_max_sierra_gas (100,000,000)` from `VersionedConstants`: [3](#0-2) 

The `initial_sierra_gas()` method in `TransactionContext` uses `l2_gas.max_amount` directly as the initial gas budget for V3 `AllResources` transactions: [4](#0-3) 

The execution engine then caps the gas per phase via `sierra_gas_limit()`: [5](#0-4) 

For Invoke/DeployAccount, the gateway ensures `l2_gas.max_amount ≤ max_l2_gas_amount` before the transaction reaches the blockifier. For Declare, no such gate exists. A Declare V3 transaction with `l2_gas.max_amount = 1_210_000_001` (one unit above the limit) passes stateless validation entirely. The stateful validator's balance check uses `max_possible_fee = l2_gas.max_amount × l2_gas.max_price_per_unit`, which for a value only slightly above the limit (e.g., `1_210_000_001 × 8_000_000_000 ≈ 9.68 × 10¹⁸ FRI`) is achievable for a funded account. The transaction is then admitted to the mempool and sequenced.

The test `valid_l2_gas_amount_on_declare` explicitly confirms this bypass is intentional and tested: [6](#0-5) 

### Impact Explanation

A Declare transaction with `l2_gas.max_amount` exceeding `max_l2_gas_amount` is admitted through the gateway without rejection. This violates the admission invariant that `max_l2_gas_amount` is the ceiling for all transaction types. The execution gas is still capped by `execute_max_sierra_gas + validate_max_sierra_gas` at runtime, so no extra gas is actually consumed. However, the gateway's resource-bound admission gate — which exists to prevent mempool pollution and enforce protocol-level gas accounting consistency — is broken for one transaction type. This matches the "High. Mempool/gateway/RPC admission accepts invalid transactions before sequencing" impact scope.

### Likelihood Explanation

Any user can submit a Declare V3 transaction with `l2_gas.max_amount` set above `max_l2_gas_amount`. The only downstream gate is the stateful balance check, which is bypassable for values only marginally above the limit. No privileged access is required. The bypass is reachable from the public RPC `starknet_addDeclareTransaction` endpoint.

### Recommendation

Remove the Declare-type exemption in `validate_resource_bounds` and apply the same `max_l2_gas_amount` ceiling to Declare transactions. If a different ceiling is appropriate for Declare (e.g., only `validate_max_sierra_gas` since Declare has no execute phase), define and enforce it explicitly rather than skipping the check entirely. The TODO comment acknowledges the gap; it should be resolved.

### Proof of Concept

1. Construct a valid Declare V3 transaction with:
   - `l2_gas.max_amount = 1_210_000_001` (one above `max_l2_gas_amount`)
   - `l2_gas.max_price_per_unit = 8_000_000_000` (at `min_gas_price`)
   - Sender account funded with ≥ `1_210_000_001 × 8_000_000_000 ≈ 9.68 × 10¹⁸` FRI
2. Submit via `starknet_addDeclareTransaction` to the gateway.
3. Stateless validation: the `max_l2_gas_amount` check is skipped for `RpcTransaction::Declare` → passes.
4. Stateful validation: balance check passes because the sender is sufficiently funded.
5. Transaction is admitted to the mempool with `l2_gas.max_amount` exceeding the gateway's declared admission limit, violating the invariant that `max_l2_gas_amount` bounds all admitted transactions.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L78-85)
```rust
        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-203)
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
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_4.json (L151-152)
```json
        "execute_max_sierra_gas": 1110000000,
        "validate_max_sierra_gas": 100000000,
```

**File:** crates/blockifier/src/context.rs (L55-73)
```rust
    pub fn initial_sierra_gas(&self) -> GasAmount {
        match &self.tx_info {
            TransactionInfo::Deprecated(_)
            | TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::L1Gas(_),
                ..
            }) => self.block_context.versioned_constants.initial_gas_no_user_l2_bound(),
            TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds { l2_gas, .. }),
                ..
            }) => {
                #[cfg(feature = "reexecution")]
                if self.block_context.versioned_constants.ignore_user_l2_gas_bound {
                    return self.block_context.versioned_constants.initial_gas_no_user_l2_bound();
                }
                l2_gas.max_amount
            }
        }
    }
```

**File:** crates/blockifier/src/blockifier_versioned_constants.rs (L405-411)
```rust
    /// Returns the maximum gas amount according to the given mode.
    pub fn sierra_gas_limit(&self, mode: &ExecutionMode) -> GasAmount {
        match mode {
            ExecutionMode::Validate => self.os_constants.validate_max_sierra_gas,
            ExecutionMode::Execute => self.os_constants.execute_max_sierra_gas,
        }
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L173-201)
```rust
#[rstest]
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200),
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(
    #[case] config: StatelessTransactionValidatorConfig,
    #[case] rpc_tx_args: RpcTransactionArgs,
) {
    let tx_type = TransactionType::Declare;
    let tx_validator = StatelessTransactionValidator { config };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_matches!(tx_validator.validate(&tx), Ok(()));
}
```
