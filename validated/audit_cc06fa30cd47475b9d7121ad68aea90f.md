### Title
`max_l2_gas_amount` Ceiling Bypassed for Declare Transactions in Stateless Validator — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The gateway's stateless validator enforces a `max_l2_gas_amount` ceiling on the `l2_gas.max_amount` resource bound for all transaction types **except** `Declare`. An attacker can submit a `Declare` transaction with `l2_gas.max_amount` set to an arbitrarily large value (up to `u64::MAX`), bypassing the admission-control ceiling entirely. The gateway accepts and forwards the transaction to the mempool, violating the invariant that no admitted transaction may claim more L2 gas than the configured ceiling.

### Finding Description

In `StatelessTransactionValidator::validate_resource_bounds`, the check for `max_l2_gas_amount` is explicitly skipped for `Declare` transactions:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← no ceiling check; falls through silently
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

The configured ceiling is `max_l2_gas_amount = 1_210_000_000` (default, also the production value in `config_schema.json`). For `Invoke` and `DeployAccount` transactions, any `l2_gas.max_amount` exceeding this value is rejected at the gateway. For `Declare`, the branch is a no-op, so the ceiling is never enforced.

The `min_gas_price` check immediately above (lines 71–76) applies to all transaction types uniformly, confirming the asymmetry is specific to the amount ceiling.

The TODO comment at line 78 explicitly acknowledges the gap: `// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.`

### Impact Explanation

**High — Mempool/gateway admission accepts an invalid transaction before sequencing.**

A `Declare` transaction with `l2_gas.max_amount = u64::MAX` passes all stateless and stateful gateway checks and is admitted to the mempool. The blockifier then uses `l2_gas.max_amount` as the execution gas limit for the transaction. With an effectively unbounded gas limit, the Declare execution phase can consume far more L2 gas than the operator intended to permit per transaction, undermining the per-transaction resource cap that `max_l2_gas_amount` is designed to enforce. This also affects the bouncer's block-capacity accounting, which relies on the declared resource bounds to decide whether a transaction fits in the current block.

### Likelihood Explanation

**High.** Any unprivileged user can craft a valid `RpcDeclareTransactionV3` with `resource_bounds.l2_gas.max_amount` set to an arbitrarily large value. No special account, key, or on-chain state is required. The bypass is unconditional — it applies to every Declare transaction regardless of sender or content. The TODO comment confirms the developers are aware the check is absent.

### Recommendation

Remove the special-case branch for `Declare` and apply the same `max_l2_gas_amount` ceiling uniformly:

```rust
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If Declare transactions legitimately require a higher gas ceiling (e.g., because compilation is more expensive), introduce a separate `max_l2_gas_amount_declare` config parameter rather than removing the check entirely.

### Proof of Concept

1. Construct an `RpcDeclareTransactionV3` with:
   - A valid Sierra contract class
   - `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)`
   - `resource_bounds.l2_gas.max_price_per_unit` ≥ `min_gas_price` (e.g., `8_000_000_001`)
2. Submit it to the gateway's `add_tx` endpoint.
3. Observe that `StatelessTransactionValidator::validate` returns `Ok(())` — the `MaxGasAmountTooHigh` error is never raised.
4. The transaction proceeds through stateful validation and is inserted into the mempool with `l2_gas.max_amount = u64::MAX`, exceeding the `1_210_000_000` ceiling that would have rejected an equivalent `Invoke` or `DeployAccount` transaction.

The test `valid_l2_gas_amount_on_declare` in `stateless_transaction_validator_test.rs` (lines 173–201) already documents and asserts this behavior as passing, confirming the bypass is present and tested as intentional. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_node/resources/config_schema.json (L3172-3176)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
```
