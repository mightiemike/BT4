### Title
Unverified Peer-Supplied `transaction_hash` in State-Sync Protobuf Conversion Allows Hash–Body Mismatch for `InvokeTransactionV3` with `proof_facts` — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The state-sync protobuf conversion `TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)` blindly trusts the `transaction_hash` field supplied by a peer without verifying it against the deserialized transaction body. Because `get_invoke_transaction_v3_hash` conditionally includes `proof_facts` in the hash preimage only when non-empty, a malicious peer can strip `proof_facts` from an `InvokeTransactionV3` while supplying the original hash (computed with `proof_facts`). The syncing node stores a body–hash pair where the hash does not match the body, causing RPC responses to return an authoritative-looking wrong value.

---

### Finding Description

**Root cause — conditional `proof_facts` inclusion in hash preimage**

`get_invoke_transaction_v3_hash` in `crates/starknet_api/src/transaction_hash.rs` appends `proof_facts` to the Poseidon hash chain only when the field is non-empty: [1](#0-0) 

This creates two distinct hash domains for otherwise identical transactions:
- **H1** = hash of `InvokeTransactionV3` with `proof_facts = []`
- **H2** = hash of `InvokeTransactionV3` with `proof_facts = [...]` (H1 ≠ H2)

**Broken invariant — no hash re-verification in state-sync deserialization**

The state-sync conversion `TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)` in `crates/apollo_protobuf/src/converters/transaction.rs` accepts the peer-supplied `transaction_hash` and the peer-supplied transaction body independently, with no cross-check: [2](#0-1) 

The `InvokeV3` protobuf message carries `proof_facts` as a `repeated Felt252` field (tag 11): [3](#0-2) 

A peer can send an `InvokeV3` message with an empty `proof_facts` list while supplying hash H2 (the hash of the original transaction that had non-empty `proof_facts`). The deserialization path `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` faithfully reconstructs `proof_facts` from the (empty) repeated field: [4](#0-3) 

The result stored in the node's DB is `(InvokeTransactionV3 { proof_facts: [] }, H2)` — a pair where the hash H2 was computed over a body that included `proof_facts`, but the stored body has `proof_facts` stripped.

**Why block-hash and state-root checks do not catch this**

The transaction commitment used in the block hash is computed from `TransactionLeafElement`, which contains only `transaction_hash` and `transaction_signature` — not the transaction body: [5](#0-4) 

Because the peer supplies the correct H2, the block-hash commitment verifies correctly. The `proof_facts` field is consumed only in `validate_proof_facts` (pre-validation), not in the execution path that produces state diffs. When the original transaction had *valid* `proof_facts` (and was therefore accepted), re-executing the stripped version also accepts it (empty `proof_facts` always passes validation at line 309 of `account_transaction.rs`), producing identical state changes and an identical state root. [6](#0-5) 

---

### Impact Explanation

After the attack, the node's storage holds `(InvokeTransactionV3 { proof_facts: [] }, H2)`. Any RPC endpoint that returns transaction data by hash H2 will return a body with `proof_facts` absent. A client that independently computes the hash of the returned body will obtain H1 ≠ H2, breaking the hash–body binding that the protocol guarantees. This matches the allowed impact: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

Any peer participating in the P2P state-sync network can perform this attack without special privileges. The only prerequisite is that at least one committed block contains an `InvokeTransactionV3` with non-empty `proof_facts` (i.e., a client-side-proving transaction). As the client-side proving feature is actively deployed, this prerequisite will be met in production.

---

### Recommendation

In `TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)`, after deserializing both the transaction body and the peer-supplied hash, recompute the hash from the body and assert equality:

```rust
let recomputed = transaction.calculate_transaction_hash(&chain_id)?;
if recomputed != tx_hash {
    return Err(ProtobufConversionError::InvalidTransactionHash { ... });
}
```

Alternatively, drop the `transaction_hash` field from the wire format entirely and always derive it locally, as is already done in the consensus path (`ConsensusTransaction` sets `transaction_hash: None` on serialization and recomputes it on deserialization via `convert_consensus_tx_to_internal_consensus_tx`). [7](#0-6) 

---

### Proof of Concept

1. A committed block contains `InvokeTransactionV3 { proof_facts: [v0, VIRTUAL_SNOS, ...], ... }` with hash H2 (computed by `get_invoke_transaction_v3_hash` with the non-empty `proof_facts` branch taken).

2. A malicious peer constructs a `protobuf::TransactionInBlock` with:
   - `txn = InvokeV3 { proof_facts: [] /* stripped */, ... /* all other fields identical */ }`
   - `transaction_hash = H2` (the original hash)

3. The syncing node calls `TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)`. It deserializes the body with `proof_facts = []` and accepts H2 as the hash without verification. The stored pair is `(InvokeTransactionV3 { proof_facts: [] }, H2)`.

4. A user calls `starknet_getTransactionByHash(H2)`. The node returns the stored body with `proof_facts` absent.

5. The user independently computes `hash(body)` = H1 ≠ H2. The node has served an authoritative-looking wrong value: the body does not correspond to the hash it was returned under. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L388-404)
```rust
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L134-185)
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
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L640-659)
```rust
        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L998-1025)
```rust
impl From<ConsensusTransaction> for protobuf::ConsensusTransaction {
    fn from(value: ConsensusTransaction) -> Self {
        match value {
            ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(
                RpcDeclareTransaction::V3(txn),
            )) => protobuf::ConsensusTransaction {
                txn: Some(protobuf::consensus_transaction::Txn::DeclareV3(txn.into())),
                transaction_hash: None,
            },
            ConsensusTransaction::RpcTransaction(RpcTransaction::DeployAccount(
                RpcDeployAccountTransaction::V3(txn),
            )) => protobuf::ConsensusTransaction {
                txn: Some(protobuf::consensus_transaction::Txn::DeployAccountV3(txn.into())),
                transaction_hash: None,
            },
            ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                RpcInvokeTransaction::V3(txn),
            )) => protobuf::ConsensusTransaction {
                txn: Some(protobuf::consensus_transaction::Txn::InvokeV3(txn.into())),
                transaction_hash: None,
            },
            ConsensusTransaction::L1Handler(txn) => protobuf::ConsensusTransaction {
                txn: Some(protobuf::consensus_transaction::Txn::L1Handler(txn.into())),
                transaction_hash: None,
            },
        }
    }
}
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/transaction.proto (L53-65)
```text
message InvokeV3 {
    Address sender = 1;
    AccountSignature signature = 2;
    repeated Felt252 calldata = 3;
    ResourceBounds resource_bounds = 4;
    uint64 tip = 5;
    repeated Felt252 paymaster_data = 6;
    repeated Felt252 account_deployment_data = 7;
    VolitionDomain nonce_data_availability_mode = 8;
    VolitionDomain fee_data_availability_mode = 9;
    Felt252 nonce = 10;
    repeated Felt252 proof_facts = 11;
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L291-303)
```rust
    let transaction_leaf_elements: Vec<TransactionLeafElement> = transactions_data
        .iter()
        .map(|tx_leaf| {
            let mut tx_leaf_element = TransactionLeafElement::from(tx_leaf);
            if starknet_version < &BlockHashVersion::V0_13_4.into()
                && tx_leaf.transaction_signature.0.is_empty()
            {
                tx_leaf_element.transaction_signature =
                    TransactionSignature(vec![Felt::ZERO].into());
            }
            tx_leaf_element
        })
        .collect();
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L306-311)
```rust
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
```
