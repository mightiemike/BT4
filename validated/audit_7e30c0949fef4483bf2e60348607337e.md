### Title
Missing `max_l2_gas_amount` Cap for `Declare` Transactions in Gateway Stateless Validation — (`crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `validate_resource_bounds` function in `StatelessTransactionValidator` enforces a `max_l2_gas_amount` ceiling on `l2_gas.max_amount` for `Invoke` and `DeployAccount` transactions, but explicitly skips this check for `Declare` transactions. An unprivileged submitter can broadcast a `Declare` transaction with `l2_gas.max_amount = u64::MAX`, which the gateway admits without error. This is the direct sequencer analog of H-21's missing `swapPath` length cap: both are per-transaction-type admission gaps where one variant of a multi-type submission path is exempted from a resource-bound check that all other variants must satisfy.

### Finding Description

In `validate_resource_bounds`:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← no check; falls through silently
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
```

The default `max_l2_gas_amount` is `1_210_000_000` (≈ 1.21 × 10⁹). For `Invoke` and `DeployAccount`, any transaction declaring more than this is rejected at the gateway. For `Declare`, the field is unconstrained: a value of `u64::MAX` (18,446,744,073,709,551,615) passes stateless validation.

The codebase itself acknowledges the gap with a `TODO` comment and has a dedicated positive test (`valid_l2_gas_amount_on_declare`) that asserts a Declare transaction with `max_amount: GasAmount(200)` passes when `max_l2_gas_amount: 100` — i.e., the bypass is tested and confirmed to work.

The `l2_gas.max_amount` field is part of the transaction hash preimage (it is hashed into the Starknet v3 transaction hash via the resource-bounds commitment). It is also the gas limit the OS enforces on the `__validate__` entry point during execution. Admitting a Declare with `l2_gas.max_amount = u64::MAX` means:

1. The gateway accepts a transaction that violates the operator-configured resource-bound policy.
2. The admitted transaction carries a gas limit orders of magnitude above the intended ceiling, which the blockifier will honour during `__validate__` execution (subject only to the separate `validate_max_n_steps` step cap in `VersionedConstants`).
3. If the gas-to-steps conversion does not saturate before reaching the step cap, the `__validate__` call can run for far longer than any Invoke or DeployAccount transaction would be permitted to.

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions before sequencing.**

Any unprivileged user can submit a `Declare` transaction with `l2_gas.max_amount = u64::MAX`. The gateway's stateless validator, which is the first and only admission gate for this field, passes it unconditionally. The transaction is then forwarded to the mempool and eventually to the blockifier with an uncapped gas limit on its `__validate__` execution. This breaks the operator's resource-bound policy for Declare transactions and can be used to exhaust sequencer execution budget during block building.

### Likelihood Explanation

**High.** The trigger requires only a single well-formed `Declare` transaction with an oversized `l2_gas.max_amount`. No privileged access, no special account state, and no multi-step setup is required. The bypass is unconditional — the code path that skips the check is taken for every `Declare` transaction regardless of any other field value.

### Recommendation

Apply the same `max_l2_gas_amount` ceiling to `Declare` transactions that is already applied to `Invoke` and `DeployAccount`:

```rust
// Remove the Declare exemption:
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If Declare transactions legitimately require a higher gas ceiling (e.g., because Sierra compilation consumes more L2 gas), introduce a separate `max_l2_gas_amount_declare` config field with an explicit, bounded value rather than leaving the field entirely unchecked.

### Proof of Concept

The existing test `valid_l2_gas_amount_on_declare` in `crates/apollo_gateway/src/stateless_transaction_validator_test.rs` already demonstrates the bypass:

```rust
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,          // ceiling = 100
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200),  // declared = 200, exceeds ceiling
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(…) {
    // asserts Ok(()) — the oversized Declare passes validation
    assert_matches!(tx_validator.validate(&tx), Ok(()));
}
```

The same test with `TransactionType::Invoke` or `TransactionType::DeployAccount` would produce `MaxGasAmountTooHigh`, confirming the Declare-specific exemption is the root cause. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L260-271)
```rust
fn test_invalid_max_l2_gas_amount(
    #[case] rpc_tx_args: RpcTransactionArgs,
    #[case] expected_error: StatelessTransactionValidatorError,
    #[values(TransactionType::DeployAccount, TransactionType::Invoke)] tx_type: TransactionType,
) {
    let tx_validator =
        StatelessTransactionValidator { config: DEFAULT_VALIDATOR_CONFIG.to_owned() };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_eq!(tx_validator.validate(&tx).unwrap_err(), expected_error);
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
