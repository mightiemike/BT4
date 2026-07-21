### Title
Missing `max_l2_gas_amount` Bound Check for Declare Transactions in Gateway Stateless Validator — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `StatelessTransactionValidator::validate_resource_bounds` function enforces an upper bound on `l2_gas.max_amount` for Invoke and DeployAccount transactions but explicitly skips this check for Declare transactions. Any unprivileged user can submit a Declare transaction with an arbitrarily large `l2_gas.max_amount` (up to `u64::MAX`) that passes all gateway validation layers and is admitted into the mempool.

### Finding Description

In `validate_resource_bounds`, the check against `config.max_l2_gas_amount` is guarded by a type-branch that short-circuits for Declare transactions:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { ... });
}
``` [1](#0-0) 

The configured limit is `max_l2_gas_amount = 1,210,000,000` (from the production config schema): [2](#0-1) 

The stateful validator's `validate_resource_bounds` only checks `l2_gas.max_price_per_unit` (via `validate_tx_l2_gas_price_within_threshold`) and never checks `max_amount`: [3](#0-2) 

There is therefore no downstream gate that enforces the `max_l2_gas_amount` bound for Declare transactions. The existing test for this check (`test_invalid_max_l2_gas_amount`) only exercises `TransactionType::DeployAccount` and `TransactionType::Invoke`, confirming Declare is untested: [4](#0-3) 

Once admitted, the `l2_gas.max_amount` field is used directly as the initial gas bound for OS execution. In the Cairo OS, `get_initial_user_gas_bound` reads `resource_bounds[L2_GAS_INDEX].max_amount` verbatim: [5](#0-4) 

In the blockifier's `max_steps` computation, for `AllResources` bounds the per-transaction step limit is derived directly from `l2_gas.max_amount / step_gas_cost`: [6](#0-5) 

With `l2_gas.max_amount = u64::MAX` and `step_gas_cost = 100`, the computed `tx_upper_bound_u64` is `≈1.8×10¹⁷`, which overflows `usize` and falls back to `usize::MAX` via the saturating path, effectively removing the per-transaction step cap for the Declare execution phase.

### Impact Explanation

A Declare transaction with `l2_gas.max_amount > max_l2_gas_amount` (e.g., `u64::MAX`) is admitted by the gateway and enters the mempool. This:

1. **Bypasses the per-transaction gas cap** for Declare transactions — the intended `max_l2_gas_amount` admission control is rendered ineffective for this transaction type.
2. **Causes incorrect fee estimation and simulation** — RPC `starknet_estimateFee` and `starknet_simulateTransactions` will compute and return authoritative-looking values based on an unbounded gas limit, producing wrong results for callers.
3. **Allows resource exhaustion** — the sequencer will attempt to execute a Declare transaction with an effectively unlimited step budget (bounded only by the block-level cap), consuming disproportionate block resources.

This matches the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing"* and *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

The trigger is fully unprivileged: any user can craft a valid Declare V3 transaction (with a real Sierra class) and set `resource_bounds.l2_gas.max_amount` to any value above `1,210,000,000`. The stateless validator explicitly skips the check (the `if let RpcTransaction::Declare(_) = tx {}` branch is a no-op), and the stateful validator never checks `max_amount`. No special account, key, or network position is required.

### Recommendation

Remove the Declare-type exemption in `validate_resource_bounds` and apply the same `max_l2_gas_amount` upper-bound check uniformly to all transaction types:

```rust
// Apply to all transaction types, including Declare.
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

Add a corresponding test case for `TransactionType::Declare` in `test_invalid_max_l2_gas_amount`.

### Proof of Concept

1. Construct a valid `RpcDeclareTransaction::V3` with any legitimate Sierra class and set:
   ```
   resource_bounds.l2_gas.max_amount = u64::MAX  // or any value > 1_210_000_000
   resource_bounds.l2_gas.max_price_per_unit     = min_gas_price (passes price check)
   ```
2. Submit to the gateway's `add_tx` endpoint.
3. `StatelessTransactionValidator::validate_resource_bounds` reaches line 79, matches `RpcTransaction::Declare(_)`, and returns `Ok(())` without checking `max_amount`.
4. `StatefulTransactionValidator::validate_resource_bounds` only calls `validate_tx_l2_gas_price_within_threshold` (price check only) — `max_amount` is never checked.
5. The transaction is admitted to the mempool with `l2_gas.max_amount = u64::MAX`.
6. During execution, `max_steps` computes `u64::MAX / 100 ≈ 1.8×10¹⁷`, which overflows `usize` and saturates to `usize::MAX`, removing the per-transaction step limit for this Declare execution.

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

**File:** crates/apollo_node/resources/config_schema.json (L3172-3176)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
        // Skip this validation during the systems bootstrap phase.
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
        }
        Ok(())
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

**File:** crates/blockifier/src/execution/entry_point.rs (L451-461)
```rust
                ValidResourceBounds::AllResources(AllResourceBounds {
                    l2_gas: ResourceBounds { max_amount, .. },
                    ..
                }) => {
                    if l2_gas_per_step.is_zero() {
                        u64::MAX
                    } else {
                        max_amount.0.saturating_div(l2_gas_per_step)
                    }
                }
            },
```
