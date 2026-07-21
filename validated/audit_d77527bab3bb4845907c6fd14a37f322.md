### Title
Declare Transactions Bypass `max_l2_gas_amount` Admission Check in `StatelessTransactionValidator` — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

---

### Summary

The `validate_resource_bounds` function in `StatelessTransactionValidator` enforces a `max_l2_gas_amount` ceiling on `l2_gas.max_amount` for Invoke and DeployAccount transactions, but explicitly skips this check for Declare transactions via a hard-coded type branch. An attacker can submit a Declare V3 transaction with `l2_gas.max_amount = u64::MAX`, bypassing the gateway's admission control entirely. This is a direct structural analog to the external bug: just as signers could bypass the `maxSigners` check by using `execTransaction` instead of `claimSigner()`, Declare transactions bypass the `max_l2_gas_amount` constraint that all other transaction types must satisfy.

---

### Finding Description

In `validate_resource_bounds`, the check reads:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
``` [1](#0-0) 

The `max_l2_gas_amount` is configured at `1,210,000,000` in the production config schema: [2](#0-1) 

For Invoke and DeployAccount, the check is enforced and the test suite confirms it: [3](#0-2) 

But the test for Declare explicitly documents that the same out-of-limit value **passes** for Declare: [4](#0-3) 

The full `validate` entry point calls `validate_resource_bounds` for all transaction types, but the Declare branch is a no-op inside that function: [5](#0-4) 

After passing stateless validation, the transaction is converted to an `InternalRpcTransaction` and forwarded to the mempool. The `convert_rpc_tx_to_internal` path for Declare transactions does not re-apply any `max_l2_gas_amount` check: [6](#0-5) 

The `InternalRpcDeclareTransactionV3` carries the unchecked `resource_bounds` field verbatim into the internal representation: [7](#0-6) 

The transaction hash is then computed over this internal representation, binding the unbounded `l2_gas.max_amount` into the canonical hash: [8](#0-7) 

---

### Impact Explanation

**High. Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

A Declare transaction with `l2_gas.max_amount = u64::MAX` (or any value exceeding `max_l2_gas_amount = 1,210,000,000`) is admitted by the gateway, forwarded to the mempool, and its hash is committed with the unbounded gas field. The gateway's admission control — whose explicit purpose is to prevent transactions with gas bounds that exceed what the block can service — is fully bypassed for the Declare type. This allows:

1. Flooding the mempool with Declare transactions carrying arbitrarily large gas bounds, consuming mempool capacity without being executable within any real block gas budget.
2. Committing transaction hashes that encode gas bounds the protocol would never allow for other transaction types, creating an asymmetric admission invariant across transaction types.

---

### Likelihood Explanation

**High.** No privilege is required. Any user can craft a `RpcDeclareTransactionV3` with `l2_gas.max_amount` set to any value above the configured limit. The gateway's stateless validator is the only admission gate, and the Declare branch is explicitly a no-op for this check. The TODO comment in the source confirms the gap is known but unaddressed.

---

### Recommendation

Remove the Declare-type exemption from `validate_resource_bounds` and apply the same `max_l2_gas_amount` ceiling to Declare transactions:

```rust
// Apply to all transaction types uniformly.
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If Declare transactions legitimately require a higher gas ceiling (e.g., due to Sierra compilation costs), introduce a separate `max_l2_gas_amount_declare` config parameter rather than removing the check entirely.

---

### Proof of Concept

1. Construct a valid `RpcDeclareTransactionV3` with `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)` and a valid Sierra contract class.
2. Submit it to the gateway via `starknet_addDeclareTransaction`.
3. The gateway calls `stateless_tx_validator.validate(&tx)`, which reaches `validate_resource_bounds`. The `if let RpcTransaction::Declare(_) = tx {}` branch is taken, and the `max_l2_gas_amount` check is skipped entirely.
4. The transaction passes all remaining stateless checks, is converted to `InternalRpcTransaction` with `resource_bounds.l2_gas.max_amount = u64::MAX` preserved, and is forwarded to the mempool.
5. Observe that an equivalent Invoke transaction with the same `l2_gas.max_amount` value is rejected with `MaxGasAmountTooHigh`, confirming the asymmetric enforcement. [9](#0-8)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-54)
```rust
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
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

**File:** crates/apollo_node/resources/config_schema.json (L3172-3176)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L243-271)
```rust
#[rstest]
#[case::max_l2_gas_amount_too_high(
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount + 1),
                max_price_per_unit: GasPrice(DEFAULT_VALIDATOR_CONFIG.min_gas_price),
            },
            ..Default::default()
        },
        ..Default::default()
    },
    StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: GasAmount(DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount + 1),
        max_gas_amount: DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount
    },
)]
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-376)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
                (
                    InternalRpcTransactionWithoutTxHash::Declare(InternalRpcDeclareTransactionV3 {
                        sender_address: tx.sender_address,
                        compiled_class_hash: tx.compiled_class_hash,
                        signature: tx.signature,
                        nonce: tx.nonce,
                        class_hash,
                        resource_bounds: tx.resource_bounds,
                        tip: tx.tip,
                        paymaster_data: tx.paymaster_data,
                        account_deployment_data: tx.account_deployment_data,
                        nonce_data_availability_mode: tx.nonce_data_availability_mode,
                        fee_data_availability_mode: tx.fee_data_availability_mode,
                    }),
                    None,
                )
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L615-628)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct InternalRpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub proof_facts: ProofFacts,
}
```
