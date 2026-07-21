The critical evidence is in `crates/apollo_p2p_sync/src/client/transaction.rs` at line 88:

```rust
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
```

The codebase itself acknowledges the missing validation. Let me trace the full path.

**Deserialization (no hash check):** `TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)` reads `transaction_hash` directly from the peer-supplied protobuf field and returns it paired with the deserialized transaction body — no recomputation, no comparison. [1](#0-0) 

**Sync client (no hash check):** `TransactionStreamFactory::parse_data_for_block` receives the `FullTransaction` (with the peer-supplied hash), and pushes the unvalidated hash straight into `block_body.transaction_hashes`. The TODO comment is the developer's own acknowledgment that this is unguarded. [2](#0-1) 

**Storage write:** The `BlockBody` (containing the forged hash) is then committed to storage via `append_body` with no intervening check. [3](#0-2) 

**No downstream reconciliation:** The block header (synced separately) carries the canonical `transaction_commitment` Merkle root. The sync path never validates that the stored per-transaction hashes are consistent with that commitment. The `calculate_block_commitments` function that builds the commitment tree is only invoked during block production, not during sync ingestion. [4](#0-3) 

---

### Title
Sync client stores peer-supplied `TransactionHash` without recomputation, allowing a malicious peer to persist a forged hash — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary
The P2P sync deserialization path trusts the `transaction_hash` field in `protobuf::TransactionInBlock` verbatim. A malicious peer can supply any 252-bit value as the hash for an otherwise structurally valid transaction body. That value is written to the node's storage as the canonical `TransactionHash` for that transaction, permanently diverging from the value that `calculate_transaction_hash` would produce over the same body.

### Finding Description
`TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)` extracts `transaction_hash` from the wire message and wraps it in `TransactionHash` without calling any hash function over the deserialized body. [5](#0-4) 

`TransactionStreamFactory::parse_data_for_block` collects these `(Transaction, TransactionHash)` pairs and appends the peer-supplied hash to `block_body.transaction_hashes`. The developer left an explicit `// TODO(eitan): Validate transaction hash from untrusted sources` comment at the push site, confirming the guard is absent. [6](#0-5) 

`BlockData::write_to_storage` then calls `storage_writer.begin_rw_txn()?.append_body(block_number, block_body)?.commit()`, persisting the forged hash to the node's database. [7](#0-6) 

The block header (synced in a separate protocol stream) carries the correct `transaction_commitment` Merkle root computed from the real hashes. No code in the sync pipeline ever cross-checks the stored per-transaction hashes against that commitment after the body is written.

### Impact Explanation
After a successful attack:
- The node's storage contains a `TransactionHash` that does not equal `calculate_transaction_hash(transaction_body)` for the affected transaction.
- All RPC endpoints that return transaction hashes (`starknet_getTransactionByHash`, `starknet_getBlockWithTxs`, etc.) serve the forged value to downstream clients.
- The stored `transaction_commitment` in the block header is inconsistent with the stored per-transaction hashes; any component that recomputes the commitment from storage (e.g., for proof generation or block-hash verification) will produce a wrong result.
- This maps to the allowed impact: **High — transaction conversion or hash logic binds the wrong hash to the executable payload.**

### Likelihood Explanation
Any peer that the syncing node connects to can exploit this. The P2P sync protocol is designed to accept connections from the open network. No operator privilege is required; the attacker only needs to be reachable as a sync peer and serve a block whose header is otherwise valid (so the header-level block-hash check passes while the per-transaction hashes are forged).

### Recommendation
In `parse_data_for_block`, after deserializing each `FullTransaction`, recompute the transaction hash from the transaction body using `calculate_transaction_hash` (with the appropriate chain id and Starknet version from the block header) and reject the peer (`BadPeerError`) if the recomputed hash differs from the peer-supplied one. This is exactly what the existing TODO comment calls for.

### Proof of Concept
```rust
// In crates/apollo_protobuf/src/converters/transaction_test.rs (illustrative)
use crate::protobuf;
use starknet_api::transaction::{Transaction, TransactionHash};
use starknet_api::block_hash::transaction_hash::calculate_transaction_hash;

let forged_hash = Felt::from(0xdeadbeefu64);
let proto = protobuf::TransactionInBlock {
    transaction_hash: Some(forged_hash.into()),
    txn: Some(protobuf::transaction_in_block::Txn::InvokeV3(
        /* valid InvokeV3 fields */
    )),
};
let (tx, stored_hash) = <(Transaction, TransactionHash)>::try_from(proto).unwrap();
let recomputed = calculate_transaction_hash(&tx, &chain_id, &starknet_version).unwrap();
// This assertion FAILS — stored_hash == forged_hash != recomputed
assert_eq!(stored_hash, recomputed);
```

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L136-184)
```rust
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

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L33-42)
```rust
        async move {
            let num_txs =
                self.0.transactions.len().try_into().expect("Failed to convert usize to u64");
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
            STATE_SYNC_BODY_MARKER.set_lossy(self.1.unchecked_next().0);
            STATE_SYNC_PROCESSED_TRANSACTIONS.increment(num_txs);
            Ok(())
        }
        .boxed()
    }
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L86-90)
```rust
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
                current_transaction_len += 1;
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L285-332)
```rust
pub async fn calculate_block_commitments(
    transactions_data: &[TransactionHashingData],
    state_diff: ThinStateDiff,
    l1_da_mode: L1DataAvailabilityMode,
    starknet_version: &StarknetVersion,
) -> (BlockHeaderCommitments, BlockCommitmentsMeasurements) {
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

    let event_leaf_elements: Vec<EventLeafElement> = transactions_data
        .iter()
        .flat_map(|transaction_data| {
            transaction_data.transaction_output.events.iter().map(|event| EventLeafElement {
                event: event.clone(),
                transaction_hash: transaction_data.transaction_hash,
            })
        })
        .collect();

    let receipt_elements: Vec<ReceiptElement> =
        transactions_data.iter().map(ReceiptElement::from).collect();

    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );

    let n_txs = transactions_data.len();
    let n_events = event_leaf_elements.len();
    let state_diff_length = state_diff.len();

    // Spawn tasks for parallel execution; each measures its own duration.
    let transaction_task = spawn_measured_task(move || {
        calculate_transaction_commitment::<Poseidon>(&transaction_leaf_elements)
    });
```
