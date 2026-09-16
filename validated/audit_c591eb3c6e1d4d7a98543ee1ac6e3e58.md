## Title
Ambiguous `ValidResourceBounds` protobuf round-trip causes gas-computation-mode and transaction-hash divergence between nodes - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
`ValidResourceBounds` has two variants — the deprecated `L1Gas` variant and the current `AllResources` variant — and the choice between them is supposed to be an explicit, authoritative discriminant carried with the transaction. However, the protobuf (P2P) decoder infers the variant independently, by inspecting whether the L2-gas and L1-data-gas bounds happen to be zero, rather than treating "which resource-bounds scheme was used" as mutually exclusive metadata that must be preserved. This mirrors the httpx2 root cause: instead of treating two related framing/interpretation fields as mutually exclusive and validating them jointly, the code performs independent `is_zero()` checks and silently reclassifies the object, producing a different in-memory representation (and therefore different transaction hash and different fee/gas validation code path) than the one the sender/gateway originally committed to.

### Finding Description
`ValidResourceBounds` is defined as: [1](#0-0) 

The RPC ingestion path (gateway) always builds the `AllResources` variant explicitly, regardless of whether `l2_gas`/`l1_data_gas` are zero: [2](#0-1) 

A gateway-accepted transaction with only L1 gas bounds set (l2_gas and l1_data_gas left at default/zero) is an explicitly valid, tested case: [3](#0-2) 

The transaction hash for this `AllResources`-typed transaction includes the L1-data-gas component in the hash (even when it is zero), because the hash function branches on the enum variant, not on the raw values: [4](#0-3) 

When this same transaction is subsequently propagated via the P2P/protobuf layer (mempool broadcast / consensus proposal transport) and deserialized on another node, the conversion code re-derives the variant purely from value inspection: [5](#0-4) 

If both `l2_gas` and `l1_data_gas` are zero, the receiving node reconstructs the transaction as `ValidResourceBounds::L1Gas` instead of `AllResources` — a different variant than what the originating gateway/sender committed to. This exact ambiguity is documented (and exploited in a test) in the codebase itself: [6](#0-5) 

The variant choice is not cosmetic — it changes two safety-critical computations that different nodes/components perform independently on the "same" transaction:
1. **Transaction hash contents** — `get_tip_resource_bounds_hash` includes the L1-data-gas felt only for the `AllResources` branch, so a variant flip changes the computed hash. [7](#0-6) 
2. **Gas/fee computation mode** — `get_gas_vector_computation_mode()` returns `NoL2Gas` for `L1Gas` and `All` for `AllResources`, which selects an entirely different resource-bounds validation and fee-checking code path in the blockifier: [8](#0-7) [9](#0-8) 

Just as httpx2's `setdefault()` treated `Content-Length` and `Transfer-Encoding` independently instead of as mutually exclusive framing signals — allowing different consumers to disagree on message boundaries — this codebase treats "is L2/data-gas bound present" as an independently inferable fact instead of an explicit, hash-committed discriminant, allowing different components (gateway vs. P2P-receiving sequencer nodes, or an OS/blockifier re-execution) to disagree on both the transaction's hash and its resource-accounting mode.

### Impact Explanation
A single unprivileged transaction sender can submit (through the gateway, a fully reachable, unprivileged path) a valid V3 transaction whose resource bounds are `AllResources` with zero L2-gas and zero L1-data-gas bounds (a validator-accepted case per the cited test). The gateway computes and commits to a transaction hash and a `GasVectorComputationMode::All` fee-validation path. Once this transaction is P2P-propagated, other/receiving nodes reconstruct it with `ValidResourceBounds::L1Gas`, causing:
- A different transaction hash to be computed if any component recomputes the hash from the P2P-deserialized transaction (e.g., during re-validation, OS replay, or receipt/commitment checks), which can produce **honest-node divergence** and prevent consensus/block confirmation for that transaction.
- Fee/resource enforcement executed under the weaker/different `NoL2Gas` semantics instead of the `All`-resources semantics the sender and gateway validated against, changing which fee checks apply and potentially bypassing L2-gas-specific price/amount enforcement that was performed at ingestion.

This satisfies "honest-node divergence" / "network unable to confirm new transactions" impact criteria required by the rules.

### Likelihood Explanation
The triggering condition (an `AllResources`-typed transaction with L2 gas and L1 data gas bounds equal to zero) is explicitly exercised as a valid, accepted case in the gateway's own test suite, meaning it is a naturally reachable and even intentionally-supported configuration, not a contrived edge case. Any unprivileged transaction sender constructing such a transaction and letting it propagate through the sequencer's P2P layer reaches the vulnerable protobuf decode path.

### Recommendation
Do not infer the `ValidResourceBounds` variant from value inspection during protobuf/P2P decoding. Instead, serialize and decode an explicit discriminant (or always decode to `AllResources` uniformly, matching the RPC path), so all nodes reconstruct an identical in-memory representation — and therefore an identical transaction hash and identical gas-computation mode — regardless of transport path.

### Proof of Concept
1. Submit (via gateway RPC) a V3 Invoke/Declare/DeployAccount transaction with `resource_bounds = AllResourceBounds { l1_gas: <non-zero>, l2_gas: ResourceBounds::default(), l1_data_gas: ResourceBounds::default() }` — accepted per `crates/apollo_gateway/src/stateless_transaction_validator_test.rs:69-82` ("valid_l1_gas" case).
2. Gateway computes `tx_hash` using `ValidResourceBounds::AllResources`, including the (zero) L1-data-gas felt in `get_tip_resource_bounds_hash` (`crates/starknet_api/src/transaction_hash.rs:187-211`), and validates fee using `GasVectorComputationMode::All`.
3. The transaction is propagated to other nodes via the P2P/protobuf layer; on decode, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (`crates/apollo_protobuf/src/converters/transaction.rs:417-437`) observes `l2_gas.is_zero() && l1_data_gas.is_zero()` and reconstructs `ValidResourceBounds::L1Gas` instead.
4. Any node/component that recomputes the transaction hash or re-derives the gas-computation mode from this P2P-reconstructed transaction obtains a different hash and a different (`NoL2Gas`) fee-validation path than the gateway that originally accepted it, exactly as demonstrated by the existing test helper `add_gas_values_to_transaction` in `crates/apollo_protobuf/src/converters/consensus_test.rs:26-48`, whose comment states the ambiguity directly: "If all the fields of `AllResources` are 0 upon serialization, then the deserialized value will be interpreted as the `L1Gas` variant."

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L368-384)
```rust
impl From<RpcDeclareTransactionV3> for DeclareTransactionV3 {
    fn from(tx: RpcDeclareTransactionV3) -> Self {
        Self {
            class_hash: tx.contract_class.calculate_class_hash(),
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            compiled_class_hash: tx.compiled_class_hash,
            sender_address: tx.sender_address,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
        }
    }
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-437)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
```

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-48)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
            }
        },
        ConsensusTransaction::L1Handler(_) => {}
    }
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L386-426)
```rust
        match tx_info {
            TransactionInfo::Current(context) => {
                let resources_amount_tuple = match &context.resource_bounds {
                    ValidResourceBounds::L1Gas(l1_gas_resource_bounds) => vec![(
                        L1Gas,
                        l1_gas_resource_bounds,
                        minimal_gas_amount_vector.to_l1_gas_for_fee(
                            tx_context.get_gas_prices(),
                            &tx_context.block_context.versioned_constants,
                        ),
                        block_info.gas_prices.l1_gas_price(fee_type),
                    )],
                    ValidResourceBounds::AllResources(AllResourceBounds {
                        l1_gas: l1_gas_resource_bounds,
                        l2_gas: l2_gas_resource_bounds,
                        l1_data_gas: l1_data_gas_resource_bounds,
                    }) => {
                        let GasPriceVector { l1_gas_price, l1_data_gas_price, l2_gas_price } =
                            block_info.gas_prices.gas_price_vector(fee_type);
                        vec![
                            (
                                L1Gas,
                                l1_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_gas,
                                *l1_gas_price,
                            ),
                            (
                                L1DataGas,
                                l1_data_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_data_gas,
                                *l1_data_gas_price,
                            ),
                            (
                                L2Gas,
                                l2_gas_resource_bounds,
                                minimal_gas_amount_vector.l2_gas,
                                *l2_gas_price,
                            ),
                        ]
                    }
                };
```
