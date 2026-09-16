### Title
Consensus/validator path hardcodes `paid_fee_on_l1 = Fee(1)` for L1Handler transactions, nullifying the anti-spam fee check enforced by the block proposer - (File: `crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary
`AccountSystem`/`GigaNameNFT` bug pattern: a payment-enforcement check references a value from the "wrong" source, which is always populated with a value that trivially satisfies the check, bypassing the intended enforcement. The sequencer has a structurally identical flaw in how `L1HandlerTransaction.paid_fee_on_l1` is populated depending on which code path produced the transaction.

### Finding Description
`L1HandlerTransaction::execute_raw` enforces that some fee was actually paid on L1 before crediting the transaction as valid: [1](#0-0) 

This check relies entirely on the `paid_fee_on_l1` field of the `L1HandlerTransaction` struct. There are two distinct ways this field gets populated depending on the code path:

1. When the sequencer's own L1 scraper observes the `sendMessageToL2` event directly on L1, it uses the real value sent with the message (see `paid_fee_on_l1: Fee(py_attr::<u128>(...))` / `Fee::from(...)` patterns and `Transaction::from_api`, which panics if this required field is missing) [2](#0-1) .

2. When an L1Handler transaction instead arrives through the **consensus/network path** — e.g. as part of a proposal received from a peer, converted from `ConsensusTransaction::L1Handler` before being handed to the batcher/blockifier for (re-)execution during proposal validation — the converter unconditionally overwrites the real fee with a hardcoded placeholder: [3](#0-2) 

This function is invoked from `convert_consensus_tx_to_internal_consensus_tx`, which is called for every L1Handler transaction that arrives as a `ConsensusTransaction` (i.e. any node validating a proposal it did not build itself, via `apollo_consensus_orchestrator/src/validate_proposal.rs` and `apollo_batcher/src/block_builder.rs`): [4](#0-3) 

Because `Fee(1) != Fee(0)`, the `paid_fee == Fee(0)` check in `execute_raw` can never fail for a validating node that receives the L1Handler transaction over the network, regardless of what fee was actually paid (or not paid) on L1. Only the original proposer — which independently scraped the real `paid_fee_on_l1` from L1 — evaluates the check against the true value.

### Impact Explanation
This produces execution divergence between the block proposer and validating nodes on the exact same L1Handler transaction:
- The proposer, using the real scraped fee, will reject/revert an L1Handler transaction whose L1 sender paid `0` (per the `InsufficientFee` check).
- Any other node re-executing that same transaction from the received proposal (during proposal validation, or Starknet OS re-execution) will always see `Fee(1)` and never trigger this rejection, executing the transaction as if a fee had been paid.

This is an honest-node execution divergence rooted in a data-integrity gap in the conversion layer: the field responsible for enforcing "a fee was paid on L1" is not carried faithfully across the consensus boundary. Note also that in `execute_raw`, `execution_state.commit()` happens *before* the `paid_fee_on_l1 == Fee(0)` check, so even on the path that does correctly detect a zero fee, the underlying storage/state effects of running the L1Handler's entry point have already been committed prior to the function returning an error — compounding the risk that state changes are not uniformly rolled back to match the "the tx was rejected" outcome across the network.

### Likelihood Explanation
Likelihood is Medium: it requires only that some L1 message is sent to L2 with no (or the check only verifies non-zero, so it requires literally paying 0) fee attached — trivially achievable by any unprivileged L1 message sender — and that the transaction is subsequently propagated through the network/consensus path rather than solely executed locally by the scraping node. Given that in a distributed sequencer topology most nodes validate proposals built by others, this path is routinely exercised.

### Recommendation
Do not hardcode a placeholder value for `paid_fee_on_l1` in `convert_consensus_l1_handler_to_internal_l1_handler`. The real, scraped value from L1 must be propagated end-to-end (e.g. included in the `ConsensusTransaction::L1Handler` payload itself, or looked up from L1 event storage before conversion), so that every node enforces the identical fee-paid check against the same ground truth. Additionally, move the `paid_fee_on_l1 == Fee(0)` check before `execution_state.commit()` in `l1_handler_transaction.rs` so a failing fee check cannot leave already-committed state changes behind.

### Proof of Concept
1. An L1 user calls `sendMessageToL2` on the Starknet core contract with `value = 0` (no fee paid), targeting an L2 contract entry point.
2. The proposer's L1 scraper observes this event and creates an `executable_transaction::L1HandlerTransaction` with `paid_fee_on_l1 = Fee(0)`. When the proposer executes it via `execute_raw`, resource checks pass, `execution_state.commit()` runs, then the `paid_fee_on_l1 == Fee(0)` check triggers `TransactionExecutionError::TransactionFeeError(InsufficientFee)`. Per current test coverage this is the expected/enforced negative-fee flow: [5](#0-4) 
3. Suppose instead the same raw `L1HandlerTransaction` reaches a different node via the network as `ConsensusTransaction::L1Handler(tx)` (e.g. as part of a proposal being validated). `convert_consensus_tx_to_internal_consensus_tx` calls `convert_consensus_l1_handler_to_internal_l1_handler`, which sets `paid_fee_on_l1 = Fee(1)` unconditionally — losing the fact that `0` was actually paid on L1.
4. When this node executes the transaction via `execute_raw`, the `paid_fee == Fee(0)` check now evaluates to `false` (since it's `Fee(1)`), so the `InsufficientFee` error is never raised, and the transaction is treated as valid/paid.
5. Result: the proposer and the validating node reach different conclusions (reject vs. accept) about the same transaction sourced from the same L1 message, an honest-node divergence directly attributable to `crates/apollo_transaction_converter/src/transaction_converter.rs:473-483`.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L100-113)
```rust
                        execution_state.commit();
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
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

**File:** crates/blockifier/src/transaction/transaction_execution.rs (L83-90)
```rust
        let executable_tx = match tx {
            StarknetApiTransaction::L1Handler(l1_handler) => {
                return Ok(Self::L1Handler(L1HandlerTransaction {
                    tx: l1_handler,
                    tx_hash,
                    paid_fee_on_l1: paid_fee_on_l1
                        .expect("L1Handler should be created with the fee paid on L1"),
                }));
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

**File:** crates/blockifier/src/transaction/transactions_test.rs (L2940-2961)
```rust
    // Negative flow: not enough fee paid on L1.

    // set the storage back to 0, so the fee will also include the storage write.
    // TODO(Meshi, 15/6/2024): change the l1_handler_set_value cairo function to
    // always update the storage instead.
    state.set_storage_at(contract_address, StorageKey::try_from(key).unwrap(), Felt::ZERO).unwrap();
    let tx_no_fee = l1handler_tx(Fee(0), contract_address);
    let error = tx_no_fee.execute(state, block_context).unwrap_err(); // Do not charge fee as L1Handler's resource bounds (/max fee) is 0.
    // Today, we check that the paid_fee is positive, no matter what was the actual fee.
    let tip = block_context.to_tx_context(&tx_no_fee).effective_tip();
    let expected_actual_fee =
        get_fee_by_gas_vector(&block_context.block_info, actual_gas_vector, &FeeType::Eth, tip);

    assert_matches!(
        error,
        TransactionExecutionError::TransactionFeeError(boxed_fee_error)
        if matches!(
            *boxed_fee_error,
            TransactionFeeError::InsufficientFee { paid_fee, actual_fee }
            if paid_fee == Fee(0) && actual_fee == expected_actual_fee
        )
    );
```
