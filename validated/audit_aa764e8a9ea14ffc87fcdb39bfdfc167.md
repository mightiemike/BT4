### Title
L1Handler `paid_fee_on_l1` is hardcoded to `Fee(1)` when reconstructing transactions from consensus, disabling the fee-payment check for L1-to-L2 messages - (File: crates/apollo_transaction_converter/src/transaction_converter.rs)

### Summary
`convert_consensus_l1_handler_to_internal_l1_handler()` reconstructs an executable `L1HandlerTransaction` from the wire-format `ConsensusTransaction::L1Handler`, but instead of using the real fee an L1 sender paid on L1, it hardcodes `Fee(1)`, with an explicit TODO admitting the value is a placeholder.

### Finding Description
`InternalConsensusTransaction::L1Handler` carries a `paid_fee_on_l1` field populated from real L1 message data by `ProposeTransactionProvider::get_l1_handler_txs`, which pulls transactions from the L1 events provider client (`crates/apollo_batcher/src/transaction_provider.rs`, lines 94-110) [1](#0-0) . However, when this transaction is serialized to the wire-level `ConsensusTransaction::L1Handler(tx.tx)` (dropping the `paid_fee_on_l1` field) and later broadcast/received/re-converted back to an executable transaction on the consensus path, `convert_consensus_l1_handler_to_internal_l1_handler` reconstructs it with a hardcoded `Fee(1)` instead of the real fee value:

```rust
fn convert_consensus_l1_handler_to_internal_l1_handler(
    &self,
    tx: transaction::L1HandlerTransaction,
) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
    Ok(executable_transaction::L1HandlerTransaction::create(
        tx,
        &self.chain_id,
        // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
        Fee(1),
    )?)
}
``` [2](#0-1) 

This call path (`convert_consensus_tx_to_internal_consensus_tx` → `convert_consensus_l1_handler_to_internal_l1_handler`) is invoked whenever a `ConsensusTransaction::L1Handler` needs to become an executable `L1HandlerTransaction` [3](#0-2) .

At execution time, `blockifier`'s `L1HandlerTransaction::execute_raw` only enforces that `paid_fee_on_l1 != Fee(0)`, treating any nonzero value as sufficient regardless of the actual fee required or actually paid on L1:

```rust
let paid_fee = self.paid_fee_on_l1;
// For now, assert only that any amount of fee was paid.
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(Box::new(
        TransactionFeeError::InsufficientFee { paid_fee, actual_fee: receipt.fee },
    )));
}
``` [4](#0-3) 

Because the hardcoded `Fee(1)` is always nonzero, this check is unconditionally satisfied whenever an L1Handler transaction reaches the consensus reconstruction path — regardless of whether the L1 sender actually paid any fee on L1. This is directly analogous to the referenced Maia finding: a cross-chain messaging fee-payment mechanism is either bypassed or not truly wired through, so the "fee paid" invariant the protocol relies on is not actually enforced end-to-end.

### Impact Explanation
The `paid_fee_on_l1` check is the sequencer's sole guard that an L1-to-L2 message actually carried a fee. Because the value is discarded and replaced with a constant `1` on the consensus reconstruction path, an L1 sender can submit `sendMessageToL2` with zero (or insufficient) fee and have the resulting L1Handler transaction always pass the "paid a fee" check for any node that reconstructs the transaction via this path, effectively disabling fee enforcement for L1-to-L2 messaging. This can also create divergent behavior between whichever component holds the real fee value (e.g., a proposer path that still has access to the original `paid_fee_on_l1`) and any path using this hardcoded reconstruction, which is a form of honest-node/executable-representation inconsistency for the same logical transaction — the same class of impact identified in the report (breaking the fee-payment invariant of a cross-domain call).

### Likelihood Explanation
The vulnerable function is exercised on every L1Handler transaction that flows through `convert_consensus_tx_to_internal_consensus_tx`, which is a core, always-active part of the consensus transaction pipeline (not a rare error path). The bug is not a hypothetical: it is explicitly flagged in-code via the `// TODO(Gilad): Change this once we put real value in paid_fee_on_l1.` comment, meaning it is a known-incomplete piece of production logic. Triggering it requires no privileged action — any account that can call the Starknet core contract's `sendMessageToL2` on L1 (an ordinary L1 message sender) can craft the underlying transaction with an arbitrary/zero fee.

### Recommendation
Preserve the actual `paid_fee_on_l1` value across the consensus wire representation (e.g., add `paid_fee_on_l1` to `ConsensusTransaction::L1Handler`, or re-derive it deterministically from the same L1 events provider/state used by the propose path) instead of hardcoding `Fee(1)` in `convert_consensus_l1_handler_to_internal_l1_handler`. Until fixed, treat any code path relying on this hardcoded value as not enforcing the L1 fee-payment invariant.

### Proof of Concept
1. An L1 account calls `sendMessageToL2` on the Starknet core contract with `msg.value = 0` (or any amount), generating an `L1HandlerTransaction` with a corresponding real `paid_fee_on_l1`.
2. This transaction is picked up by the L1 events provider and turned into a `ConsensusTransaction::L1Handler(tx.tx)` for broadcast, which drops the `paid_fee_on_l1` field (see `convert_internal_consensus_tx_to_consensus_tx`, lines 178-180) [5](#0-4) .
3. Any node reconstructing the executable transaction from this `ConsensusTransaction` calls `convert_consensus_l1_handler_to_internal_l1_handler`, which sets `paid_fee_on_l1 = Fee(1)` unconditionally [2](#0-1) .
4. During execution, `L1HandlerTransaction::execute_raw`'s fee check only rejects `Fee(0)`, so the hardcoded `Fee(1)` always passes regardless of what was truly paid on L1 [4](#0-3) .

I was not able to fully trace every consumer of `convert_consensus_tx_to_internal_consensus_tx` (e.g., exact validator vs. proposer call sites in `apollo_consensus_orchestrator`) within the available search budget, so I cannot definitively confirm whether the proposer's own local execution path also goes through this same hardcoded-fee reconstruction or retains the real fee via a separate code path — this would need further investigation (ideally via a Devin session with full repo access) to determine whether there is an additional cross-node consistency angle beyond the fee-check bypass itself.

### Citations

**File:** crates/apollo_batcher/src/transaction_provider.rs (L94-110)
```rust
    async fn get_l1_handler_txs(
        &mut self,
        n_txs: usize,
    ) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
        Ok(self
            .l1_events_provider_client
            .get_txs(n_txs, self.height)
            .await
            .inspect_err(|err| {
                warn!("L1 provider error while fetching L1 handler transactions: {:?}", err);
                BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
            })
            .unwrap_or_default()
            .into_iter()
            .map(InternalConsensusTransaction::L1Handler)
            .collect())
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L178-180)
```rust
            InternalConsensusTransaction::L1Handler(tx) => {
                Ok(ConsensusTransaction::L1Handler(tx.tx))
            }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L184-202)
```rust
    async fn convert_consensus_tx_to_internal_consensus_tx(
        &self,
        tx: ConsensusTransaction,
    ) -> TransactionConverterResult<(InternalConsensusTransaction, Option<VerifyAndStoreProofTask>)>
    {
        match tx {
            ConsensusTransaction::RpcTransaction(tx) => {
                let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
                let task = proof_data.map(|(proof_facts, proof)| {
                    self.spawn_verify_and_store_proof(proof_facts, proof)
                });
                Ok((InternalConsensusTransaction::RpcTransaction(internal_tx), task))
            }
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
            }
        }
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L473-483)
```rust
    fn convert_consensus_l1_handler_to_internal_l1_handler(
        &self,
        tx: transaction::L1HandlerTransaction,
    ) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
        Ok(executable_transaction::L1HandlerTransaction::create(
            tx,
            &self.chain_id,
            // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
            Fee(1),
        )?)
    }
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L103-113)
```rust
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```
