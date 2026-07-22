### Title
Missing `max_l2_gas_amount` Admission Check for Declare Transactions Allows Unbounded L2 Gas Claims at Gateway - (File: crates/apollo_gateway/src/stateless_transaction_validator.rs)

### Summary
The `StatelessTransactionValidator::validate_resource_bounds` function explicitly skips the `max_l2_gas_amount` upper-bound check for `RpcTransaction::Declare` transactions. Any unprivileged user can submit a Declare transaction whose `l2_gas.max_amount` field exceeds the configured gateway limit (1,210,000,000 in production), bypassing the admission control invariant that is enforced for every other transaction type.

### Finding Description
In `crates/apollo_gateway/src/stateless_transaction_validator.rs`, the `validate_resource_bounds` function enforces that `l2_gas.max_amount ≤ config.max_l2_gas_amount` for Invoke and DeployAccount transactions. However, for Declare transactions the check is unconditionally skipped via an empty `if let` branch, with only a TODO comment acknowledging the gap:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← no check performed; falls through
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
```

The production default for `max_l2_gas_amount` is **1,210,000,000** and `min_gas_price` is **8,000,000,000** (8 Gwei). The test `valid_l2_gas_amount_on_declare` explicitly asserts that a Declare transaction with `max_amount = 200` passes when `max_l2_gas_amount = 100`, confirming this is the live behavior.

The `l2_gas.max_amount` field is not merely advisory: the Starknet OS reads it directly as the per-transaction gas limit via `get_initial_user_gas_bound`, which returns `resource_bounds[L2_GAS_INDEX].max_amount`. A Declare transaction with an arbitrarily large value therefore executes under a correspondingly large gas budget, unconstrained by the gateway's admission policy.

### Impact Explanation
**High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

A Declare transaction with `l2_gas.max_amount` set to any value above 1,210,000,000 (e.g., 2,000,000,000 or higher) passes stateless validation without error. If the account holds sufficient balance to cover `max_possible_fee = max_amount × max_price_per_unit`, it also passes stateful validation. The transaction is then admitted to the mempool and forwarded to the batcher, where the blockifier executes it with the inflated gas limit supplied by the OS. This breaks the admission-control invariant that `max_l2_gas_amount` is supposed to enforce uniformly across all transaction types.

### Likelihood Explanation
Likelihood is **High**. The bypass requires no special privilege: any account that can submit a Declare transaction (i.e., any account with a valid Sierra contract and sufficient balance) can set `l2_gas.max_amount` to an arbitrary value. The code path is unconditional and is explicitly tested as passing. No existing gate downstream of the gateway re-enforces the `max_l2_gas_amount` limit before execution.

### Recommendation
Remove the Declare-specific exemption and apply the same `max_l2_gas_amount` check uniformly to all transaction types:

```rust
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If Declare transactions legitimately require a higher gas budget, introduce a separate `max_l2_gas_amount_declare` configuration field rather than removing the check entirely.

### Proof of Concept
The existing test `valid_l2_gas_amount_on_declare` already demonstrates the bypass:

```rust
// crates/apollo_gateway/src/stateless_transaction_validator_test.rs:173-201
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,          // limit = 100
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200), // amount = 200 > 100
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(…) {
    let tx_type = TransactionType::Declare;
    // …
    assert_matches!(tx_validator.validate(&tx), Ok(())); // passes — no error
}
```

Against the production default (`max_l2_gas_amount = 1_210_000_000`, `min_gas_price = 8_000_000_000`), an attacker submits:

```
RpcDeclareTransactionV3 {
    resource_bounds: AllResourceBounds {
        l1_gas:      { max_amount: 1,          max_price_per_unit: 1 },
        l2_gas:      { max_amount: 10_000_000_000,  // 10× the limit
                       max_price_per_unit: 8_000_000_000 },
        l1_data_gas: { max_amount: 0,          max_price_per_unit: 0 },
    },
    // valid Sierra contract class, valid nonce, valid signature …
}
```

Stateless validation passes (the Declare branch is empty). Stateful validation passes if the account balance ≥ `10_000_000_000 × 8_000_000_000 = 8×10^19` (achievable with a well-funded account). The transaction enters the mempool and is executed by the blockifier with `l2_gas.max_amount = 10,000,000,000` as the OS-enforced gas limit — 8.26× the intended maximum.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L74-78)
```text
// Returns the transaction's initial gas derived from its resource bounds.
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
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
