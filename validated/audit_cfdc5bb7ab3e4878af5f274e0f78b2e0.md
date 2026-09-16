### Title
Hardcoded `Fee(1)` in Consensus L1Handler Conversion Disables L1-Paid-Fee Enforcement - ([File: crates/apollo_transaction_converter/src/transaction_converter.rs])

### Summary
The `TransactionConverter::convert_consensus_l1_handler_to_internal_l1_handler` function reconstructs an internal `L1HandlerTransaction` from a consensus-received `L1HandlerTransaction` by hardcoding the `paid_fee_on_l1` field to `Fee(1)`, discarding whatever fee was actually paid on L1 by the message sender. This is the same bug class as the reference report: a value that is supposed to reflect a real cross-chain amount (there, the `xcall` asset `total`; here, the L1-paid fee accompanying an L1→L2 message) is replaced by a constant placeholder instead of the real value.

### Finding Description
`ConsensusTransaction::L1Handler` only carries the raw `starknet_api::transaction::L1HandlerTransaction` (contract address, selector, calldata, nonce) — it does not carry `paid_fee_on_l1`, since that value is external L1 metadata tracked separately by the L1 scraper/provider. When any node (validator or re-executing node) converts a consensus-level L1Handler transaction into the internal, executable representation, it calls: [1](#0-0) 

which hardcodes `Fee(1)` with a TODO acknowledging the placeholder: "Change this once we put real value in `paid_fee_on_l1`."

This `paid_fee_on_l1` value is exactly what `blockifier`'s L1Handler execution path uses to enforce that the sender actually paid a fee on L1: [2](#0-1) 

The check only rejects the transaction `if paid_fee == Fee(0)`. Because the converter always injects `Fee(1)` (never `0`), this check can never fail for any L1Handler transaction that goes through the consensus conversion path — regardless of what was truly paid on L1 (including nothing, if the real amount were somehow `0`, or an amount insufficient for the resources consumed).

### Impact Explanation
The `paid_fee_on_l1` check is the sequencer/OS-side enforcement that an L1 message sender paid for their message's L2 execution. Hardcoding `Fee(1)` makes this enforcement a dead check on the consensus/re-execution path: it always passes independent of the real L1 payment, exactly mirroring the reference bug where the cross-chain `total` parameter was overridden with a constant instead of the caller-supplied amount. This allows L1Handler transactions to consume sequencer resources (bounded only by `l1_handler_max_amount_bounds`) while the fee-payment invariant is not actually verified by the code that runs during consensus block building/validation, which is a resource-accounting/fee-enforcement correctness bug reachable by any L1 message sender.

Because all nodes apply the same hardcoded value consistently, this does not by itself cause a state/root divergence between honest nodes, but it does defeat the intended "was fee paid on L1" safety check for the consensus execution path, which is a concrete unauthorized-action class issue (bypassing a payment/fee precondition for a network-level action).

### Likelihood Explanation
Every L1Handler transaction that flows through `convert_consensus_tx_to_internal_consensus_tx` (i.e., anything received via consensus rather than directly from the L1 scraper's original event ingestion) is affected, since it is the standard/only path to reconstruct the internal L1Handler with a fee value before execution in that flow. It requires no privileged access — an ordinary L1 message sender can trigger the affected code path just by having their message included in a block.

### Recommendation
Do not hardcode `Fee(1)`. Preserve and propagate the real `paid_fee_on_l1` value (as tracked by the L1 scraper/provider for the corresponding L1-to-L2 message) through the consensus transaction representation instead of dropping it, or fetch the real fee from the same authoritative source the proposer used, so `convert_consensus_l1_handler_to_internal_l1_handler` reconstructs the transaction with the actual paid amount, restoring the `Fee(0)` insufficient-fee check's validity.

### Proof of Concept
1. An L1 message sender sends a message to L2 (an L1Handler transaction) on the base layer, optionally with `paid_fee_on_l1 = 0` or an insufficient amount.
2. The transaction is scraped and proposed as a `ConsensusTransaction::L1Handler`, which drops the `paid_fee_on_l1` field entirely.
3. Any node (validator, or the same node during a later re-execution/replay via the consensus path) calls `convert_consensus_tx_to_internal_consensus_tx` → `convert_consensus_l1_handler_to_internal_l1_handler`, which sets `paid_fee_on_l1 = Fee(1)` unconditionally: [3](#0-2) 
4. During execution, `L1HandlerTransaction::execute_raw` checks only `paid_fee == Fee(0)` [2](#0-1) , which never triggers since the injected value is always `1`, so the transaction is accepted regardless of the true L1 payment.

### Citations

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
