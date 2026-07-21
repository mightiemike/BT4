### Title
`ValidResourceBounds` Protobuf Deserialization Silently Downgrades `AllResources` to `L1Gas` When Both L2/L1-Data Gas Are Zero, Producing a Wrong Transaction Hash for Synced Transactions - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation silently produces `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. This is correct for pre-0.13.3 V3 transactions (which were originally signed with only two resource bounds), but it is **incorrect** for post-0.13.3 `AllResources` transactions where both fields happen to be zero. Because `get_tip_resource_bounds_hash` hashes a different number of elements for `L1Gas` (3: tip + l1_gas + l2_gas) versus `AllResources` (4: tip + l1_gas + l2_gas + l1_data_gas), the deserialized transaction carries a structurally different hash preimage than the one the signer committed to. Any downstream component that recomputes the hash from the deserialized `Transaction` object will produce a divergent value.

---

### Finding Description

**Invariant broken:** A V3 transaction submitted through the RPC is always represented as `AllResourceBounds` (the `AllResources` variant). Its hash is computed over four elements: `[tip, l1_gas_packed, l2_gas_packed, l1_data_gas_packed]`. When that same transaction is later deserialized from the P2P sync wire format, the `ValidResourceBounds` converter silently collapses it to `L1Gas` if both `l2_gas` and `l1_data_gas` are zero, and the hash is then computed over only three elements: `[tip, l1_gas_packed, l2_gas_packed]`. The two hashes are structurally distinct Poseidon digests.

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 417-436
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    ...
    Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
        ValidResourceBounds::L1Gas(l1_gas)   // ← wrong for post-0.13.3 AllResources txs
    } else {
        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
    })
}
``` [1](#0-0) 

The heuristic "if both are zero → L1Gas" was introduced to distinguish old pre-0.13.3 transactions from new ones. However, a post-0.13.3 transaction is perfectly valid with `l2_gas = 0` and `l1_data_gas = 0` (only `l1_gas` non-zero). The gateway's stateless validator explicitly allows this: [2](#0-1) 

**Hash divergence — `get_tip_resource_bounds_hash`:**

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 188-211
pub fn get_tip_resource_bounds_hash(...) -> Result<Felt, StarknetApiError> {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,   // zero for both variants
    ];
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],             // ← 3-element hash
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 4-element hash
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
``` [3](#0-2) 

For a transaction with `l1_gas = X`, `l2_gas = 0`, `l1_data_gas = 0`:

| Variant at hash time | Elements hashed | Result |
|---|---|---|
| `AllResources` (RPC/consensus path) | `[tip, X, 0, 0]` | `H_A` |
| `L1Gas` (sync deserialization) | `[tip, X, 0]` | `H_B ≠ H_A` |

The consensus and RPC paths are **not** affected because they use `AllResourceBounds` directly: [4](#0-3) 

The sync path is affected because it goes through `TryFrom<protobuf::transaction_in_block::Txn::InvokeV3> for InvokeTransactionV3` → `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: [5](#0-4) 

The test file for the consensus converter explicitly documents this collapse and works around it by forcing non-zero gas values: [6](#0-5) 

No equivalent guard exists in the sync deserialization path.

---

### Impact Explanation

Any component that recomputes a transaction hash from the sync-deserialized `Transaction` object will produce `H_B` instead of the canonical `H_A`. Concretely:

1. **`validate_transaction_hash`** — called during block sync to verify that the wire-supplied hash matches the locally recomputed hash. For an `AllResources` transaction with zero L2/L1-data gas, the recomputed hash is `H_B` while the wire hash is `H_A`; validation returns `false`, and the syncing node rejects the block as invalid. This is a **wrong state / revert result from accepted input** and a **transaction conversion binding the wrong hash**.

2. **Fee/execution divergence** — `ValidResourceBounds::L1Gas` selects `GasVectorComputationMode::NoL2Gas`, while `AllResources` selects `GasVectorComputationMode::All`. A syncing node executing the transaction under the wrong mode computes different gas vectors, fees, and potentially different execution outcomes, causing **state divergence** between the proposing node and syncing nodes.

This matches the allowed impact: **"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."** and **"Critical. Wrong state … or revert result from blockifier/syscall/execution logic for accepted input."**

---

### Likelihood Explanation

Any unprivileged user can submit a valid V3 transaction through the public RPC with `l1_gas > 0`, `l2_gas = 0`, `l1_data_gas = 0`. The gateway accepts it (the stateless validator explicitly allows single-resource bounds). The transaction is included in a block. Every syncing peer that deserializes the block via the P2P sync path will hit the downgrade and compute the wrong hash. No special privileges, malformed bytes, or adversarial peers are required — a normal user transaction is sufficient.

---

### Recommendation

Replace the zero-value heuristic with an explicit version/context signal. The protobuf `ResourceBounds` message already carries `l1_data_gas` as an `optional` field; its **presence** (even when zero) should be the discriminator, not its value:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    ...
    // Use field presence, not zero-value, to distinguish L1Gas from AllResources.
    Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
        ValidResourceBounds::L1Gas(l1_gas)
    } else {
        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
    })
}
``` [7](#0-6) 

This preserves backward compatibility with 0.13.2 peers (which omit `l1_data_gas`) while correctly round-tripping post-0.13.3 `AllResources` transactions that happen to have zero L2/L1-data gas values.

---

### Proof of Concept

1. Submit an Invoke V3 transaction via the RPC with:
   - `resource_bounds.l1_gas = { max_amount: 1000, max_price_per_unit: 1 }`
   - `resource_bounds.l2_gas = { max_amount: 0, max_price_per_unit: 0 }`
   - `resource_bounds.l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }`

2. The gateway accepts it. `convert_rpc_tx_to_internal` computes the hash via `InternalRpcInvokeTransactionV3::calculate_transaction_hash` → `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash` with `ValidResourceBounds::AllResources(...)`, producing `H_A = poseidon(tip, l1_gas_packed, 0, 0)`.

3. The transaction is included in a block and broadcast over P2P.

4. A syncing peer deserializes the block via `TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)`. The `ResourceBounds` protobuf has `l1_data_gas = Some(zero)` and `l2_gas = zero`, so `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` returns `ValidResourceBounds::L1Gas(l1_gas)`.

5. The syncing peer recomputes the hash via `Transaction::calculate_transaction_hash` → `get_tip_resource_bounds_hash` with `ValidResourceBounds::L1Gas(...)`, producing `H_B = poseidon(tip, l1_gas_packed, 0)`.

6. `H_A ≠ H_B`. `validate_transaction_hash` returns `false`. The syncing peer rejects the block. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L134-184)
```rust
impl TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash) {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::TransactionInBlock) -> Result<Self, Self::Error> {
        let tx_hash = value
            .transaction_hash
            .clone()
            .ok_or(missing("Transaction::transaction_hash"))?
            .try_into()
            .map(TransactionHash)?;
        let txn = value.txn.ok_or(missing("Transaction::txn"))?;
        let transaction: Transaction = match txn {
            protobuf::transaction_in_block::Txn::DeclareV0(declare_v0) => Transaction::Declare(
                DeclareTransaction::V0(DeclareTransactionV0V1::try_from(declare_v0)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV1(declare_v1) => Transaction::Declare(
                DeclareTransaction::V1(DeclareTransactionV0V1::try_from(declare_v1)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV2(declare_v2) => Transaction::Declare(
                DeclareTransaction::V2(DeclareTransactionV2::try_from(declare_v2)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV3(declare_v3) => Transaction::Declare(
                DeclareTransaction::V3(DeclareTransactionV3::try_from(declare_v3)?),
            ),
            protobuf::transaction_in_block::Txn::Deploy(deploy) => {
                Transaction::Deploy(DeployTransaction::try_from(deploy)?)
            }
            protobuf::transaction_in_block::Txn::DeployAccountV1(deploy_account_v1) => {
                Transaction::DeployAccount(DeployAccountTransaction::V1(
                    DeployAccountTransactionV1::try_from(deploy_account_v1)?,
                ))
            }
            protobuf::transaction_in_block::Txn::DeployAccountV3(deploy_account_v3) => {
                Transaction::DeployAccount(DeployAccountTransaction::V3(
                    DeployAccountTransactionV3::try_from(deploy_account_v3)?,
                ))
            }
            protobuf::transaction_in_block::Txn::InvokeV0(invoke_v0) => Transaction::Invoke(
                InvokeTransaction::V0(InvokeTransactionV0::try_from(invoke_v0)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV1(invoke_v1) => Transaction::Invoke(
                InvokeTransaction::V1(InvokeTransactionV1::try_from(invoke_v1)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV3(invoke_v3) => Transaction::Invoke(
                InvokeTransaction::V3(InvokeTransactionV3::try_from(invoke_v3)?),
            ),
            protobuf::transaction_in_block::Txn::L1Handler(l1_handler) => {
                Transaction::L1Handler(L1HandlerTransaction::try_from(l1_handler)?)
            }
        };
        Ok((transaction, tx_hash))
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
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

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L188-211)
```rust
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L169-192)
```rust
impl TryFrom<protobuf::DeclareV3WithClass> for RpcDeclareTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeclareV3WithClass) -> Result<Self, Self::Error> {
        let (common, class) = value.try_into()?;
        Ok(Self {
            resource_bounds: match common.resource_bounds {
                ValidResourceBounds::AllResources(resource_bounds) => resource_bounds,
                _ => {
                    return Err(DEPRECATED_RESOURCE_BOUNDS_ERROR);
                }
            },
            sender_address: common.sender_address,
            signature: common.signature,
            nonce: common.nonce,
            compiled_class_hash: common.compiled_class_hash,
            contract_class: class,
            tip: common.tip,
            paymaster_data: common.paymaster_data,
            account_deployment_data: common.account_deployment_data,
            nonce_data_availability_mode: common.nonce_data_availability_mode,
            fee_data_availability_mode: common.fee_data_availability_mode,
        })
    }
}
```

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-47)
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
```
