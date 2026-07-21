### Title
Missing `max_l2_gas_amount` Admission Check for Declare Transactions Allows Unbounded Gas Claims at Gateway - (File: crates/apollo_gateway/src/stateless_transaction_validator.rs)

### Summary

The gateway's stateless validator enforces a `max_l2_gas_amount` cap on `l2_gas.max_amount` for Invoke and DeployAccount transactions, but explicitly skips this check for Declare transactions. An unprivileged user can submit a `RpcDeclareTransaction::V3` with `l2_gas.max_amount = u64::MAX`, bypass the admission gate, and have the transaction accepted into the mempool with a saturated `max_possible_fee`, distorting mempool ordering and violating the gateway's own resource-bound invariant.

### Finding Description

In `StatelessTransactionValidator::validate_resource_bounds`, the `max_l2_gas_amount` upper-bound check is guarded by a type branch that silently no-ops for Declare transactions:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← no check performed
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
``` [1](#0-0) 

The production configuration sets `max_l2_gas_amount = 1_210_000_000` (1.21 billion gas units): [2](#0-1) [3](#0-2) 

This limit is intentionally enforced for Invoke and DeployAccount, but the TODO comment and the dedicated test `valid_l2_gas_amount_on_declare` explicitly confirm the gap is present and accepted: [4](#0-3) 

The test proves that a Declare with `max_amount = 200` passes validation when `max_l2_gas_amount = 100` — a 2× violation — and the same logic applies to `u64::MAX`.

### Impact Explanation

Once admitted, the `InternalRpcTransaction` carries the unchecked `resource_bounds` field verbatim. The mempool uses `max_possible_fee` for transaction ordering, which is computed with saturating arithmetic: [5](#0-4) 

With `l2_gas.max_amount = u64::MAX` and any non-zero `max_price_per_unit` (the gateway's `min_gas_price` check still applies, so the minimum is 8 billion), the `l2_gas` term saturates to `Fee::MAX`. The Declare transaction appears to have the highest possible fee, allowing it to jump ahead of all legitimate transactions in the mempool queue. This is a direct violation of the "Mempool/gateway/RPC admission accepts invalid transactions before sequencing" impact category.

### Likelihood Explanation

The trigger requires only a standard V3 Declare transaction with an oversized `l2_gas.max_amount` field. No privileged access, special account, or malformed bytes are needed — the transaction is structurally valid and passes all other stateless checks (Sierra version, class size, signature, DA modes). The attack is fully unprivileged and reproducible with any standard Starknet SDK.

### Recommendation

Remove the Declare-specific exemption and apply the same `max_l2_gas_amount` upper-bound check uniformly to all transaction types, or introduce a separate, appropriately sized cap for Declare transactions if their gas profile genuinely differs. The TODO comment should be resolved rather than left open in production code.

```rust
// Apply max_l2_gas_amount check to all transaction types, including Declare.
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

### Proof of Concept

1. Construct a valid `RpcDeclareTransaction::V3` with any legitimate Sierra class.
2. Set `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)` and `resource_bounds.l2_gas.max_price_per_unit = GasPrice(8_000_000_000)` (the minimum allowed by `min_gas_price`).
3. Submit to the gateway's `add_tx` endpoint.
4. The `validate_resource_bounds` call at line 79 matches `RpcTransaction::Declare(_)` and returns `Ok(())` without checking the amount.
5. The transaction is forwarded to the mempool with `max_possible_fee` saturated to `u128::MAX`.
6. The transaction is ordered ahead of all other pending transactions regardless of their actual fee bids.

The existing test `valid_l2_gas_amount_on_declare` in `crates/apollo_gateway/src/stateless_transaction_validator_test.rs` (lines 173–201) already documents and asserts this behavior as passing, confirming the bypass is reachable in the current production code path. [6](#0-5)

### Citations

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

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L25-25)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": 1210000000,
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

**File:** crates/starknet_api/src/transaction/fields.rs (L393-414)
```rust
    pub fn max_possible_fee(&self, tip: Tip) -> Fee {
        match self {
            ValidResourceBounds::L1Gas(l1_bounds) => {
                l1_bounds.max_amount.saturating_mul(l1_bounds.max_price_per_unit)
            }
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => l1_gas
                .max_amount
                .saturating_mul(l1_gas.max_price_per_unit)
                .saturating_add(
                    l2_gas
                        .max_amount
                        .saturating_mul(l2_gas.max_price_per_unit.saturating_add(tip.into())),
                )
                .saturating_add(
                    l1_data_gas.max_amount.saturating_mul(l1_data_gas.max_price_per_unit),
                ),
        }
    }
```
