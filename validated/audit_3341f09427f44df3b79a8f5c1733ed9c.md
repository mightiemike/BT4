### Title
`ValidResourceBounds` Variant Collapse in Protobuf Deserialization Produces a Different Transaction Hash Preimage Than the One Committed at the Gateway — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently collapses an `AllResources` variant (with zero `l2_gas` and `l1_data_gas`) into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` includes an extra `L1_DATA_GAS` felt in the Poseidon preimage only for `AllResources`, the transaction hash computed at the gateway differs from the hash recomputed by any node that deserializes the same transaction over P2P. A V3 invoke transaction with only non-zero `l1_gas` passes gateway admission, is committed under hash H1, and is then re-hashed as H2 ≠ H1 by every syncing peer.

---

### Finding Description

**Step 1 – The protobuf deserializer collapses the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` applies a zero-check:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changes
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

An `AllResources` value whose `l2_gas` and `l1_data_gas` are both zero is therefore round-tripped as `L1Gas`. The symmetric serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`) emits identical wire bytes for both variants when those fields are zero, so the information about which variant was originally used is permanently lost.

**Step 2 – The hash function is variant-sensitive.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the Poseidon preimage differently for the two variants:

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // ← 2 felts
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 3 felts
    }
});
``` [2](#0-1) 

- **`L1Gas` path**: `Poseidon(tip ‖ concat(L1_GAS,A,P) ‖ concat(L2_GAS,0,0))` — 2 resource felts.
- **`AllResources` path (zero l2/l1_data)**: `Poseidon(tip ‖ concat(L1_GAS,A,P) ‖ concat(L2_GAS,0,0) ‖ concat(L1_DATA_GAS,0,0))` — 3 resource felts.

These are structurally different Poseidon inputs and produce different digests.

**Step 3 – The gateway always commits the `AllResources` hash.**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`). [3](#0-2) 

`convert_rpc_tx_to_internal` wraps it as `ValidResourceBounds::AllResources(tx.resource_bounds)` and immediately calls `calculate_transaction_hash`, binding hash H1 (3-felt preimage). [4](#0-3) 

**Step 4 – A transaction with only non-zero `l1_gas` passes gateway admission.**

The stateless validator's `validate_resource_bounds` only requires `max_possible_fee > 0` and `l2_gas.max_price_per_unit >= min_gas_price`. With `min_gas_price = 0` (or any config where the check is disabled), a transaction with `l1_gas = {amount: N, price: P}` and `l2_gas = l1_data_gas = 0` is accepted. [5](#0-4) 

The test case `valid_l1_gas` explicitly confirms this is an accepted input. [6](#0-5) 

**Step 5 – After P2P round-trip, the hash is recomputed as H2 ≠ H1.**

When the block is synced, `InvokeTransactionV3` (which holds `resource_bounds: ValidResourceBounds`) is serialized to protobuf and deserialized. The zero-check fires, producing `L1Gas`. Any subsequent call to `get_invoke_transaction_v3_hash` on the deserialized struct uses the 2-felt preimage, yielding H2 ≠ H1. [7](#0-6) 

`validate_transaction_hash` compares the stored hash against the recomputed hash; it will return `false` for every such transaction. [8](#0-7) 

---

### Impact Explanation

Every syncing node that deserializes a V3 invoke transaction with `AllResources{l2_gas=0, l1_data_gas=0}` over P2P will compute a hash that does not match the hash committed by the proposing node. This breaks transaction hash validation on the sync path, causing syncing nodes to reject otherwise-valid blocks. The result is a reachable consensus/state split: the proposing node's committed state diverges from every peer that re-derives the hash from the wire representation.

This matches the allowed impact: **"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

The trigger is a standard V3 invoke transaction with only `l1_gas` set (a pattern that existed before Starknet 0.13.3 and is still accepted by the gateway). No privileged access is required. The attacker only needs to submit one such transaction that gets included in a block; every syncing peer will then fail to validate it.

---

### Recommendation

1. **Fix the protobuf deserializer**: preserve `AllResources` unconditionally for V3 transactions. The zero-check was introduced for backward compatibility with pre-0.13.3 blocks; it should be gated on block version, not on field values.

```rust
// Instead of the zero-check, always produce AllResources for V3:
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

2. **Alternatively, canonicalize the hash function**: make `get_tip_resource_bounds_hash` always emit the 3-felt form (including `L1_DATA_GAS` even when zero) for all V3 transactions, so `L1Gas` and `AllResources{l2=0,l1d=0}` produce the same digest.

---

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit to gateway → passes stateless validator (max_possible_fee = 1000 > 0).

3. Gateway calls convert_rpc_tx_to_internal:
     tx_without_hash = InternalRpcTransactionWithoutTxHash::Invoke(...)
     // resource_bounds() returns ValidResourceBounds::AllResources(...)
     H1 = get_invoke_transaction_v3_hash(...)
        = Poseidon(INVOKE ‖ version ‖ sender ‖
            Poseidon(tip ‖ concat(L1_GAS,1000,1) ‖ concat(L2_GAS,0,0) ‖ concat(L1_DATA_GAS,0,0))
            ‖ ...)   // 3-felt resource preimage

4. Transaction included in block with stored hash = H1.

5. Syncing node receives block over P2P:
     protobuf::ResourceBounds { l1_gas: (1000,1), l2_gas: (0,0), l1_data_gas: (0,0) }
     → TryFrom fires zero-check → ValidResourceBounds::L1Gas((1000,1))

6. Syncing node recomputes hash:
     H2 = get_invoke_transaction_v3_hash(...)
        = Poseidon(INVOKE ‖ version ‖ sender ‖
            Poseidon(tip ‖ concat(L1_GAS,1000,1) ‖ concat(L2_GAS,0,0))
            ‖ ...)   // 2-felt resource preimage

7. validate_transaction_hash(tx, block_number, chain_id, H1, options)
     → recomputes H2 ≠ H1 → returns false → block rejected.
```

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/starknet_api/src/transaction_hash.rs (L197-210)
```rust
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
```

**File:** crates/starknet_api/src/transaction_hash.rs (L370-404)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L551-566)
```rust
pub struct RpcInvokeTransactionV3 {
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
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
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
