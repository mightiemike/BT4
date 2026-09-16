### Title
Consensus L1Handler reconstruction discards the real `paid_fee_on_l1`, causing execution divergence on the fee sufficiency check - (File: crates/apollo_transaction_converter/src/transaction_converter.rs)

### Summary
The external report's bug class is "an array element that should be individually validated is silently accepted as zero, bypassing an intended non-zero check and letting the attacker obtain benefit without paying." In the sequencer, the analogous `paid_fee_on_l1` field of an `L1HandlerTransaction` is exactly such a value that gates a non-zero-fee check inside `execute_raw`, but the consensus reconstruction path hardcodes it to a fixed placeholder (`Fee(1)`) instead of using the value that was actually observed/paid on L1, decoupling the check from the true input.

### Finding Description
`L1HandlerTransaction::execute_raw` enforces that `self.paid_fee_on_l1 != Fee(0)` as the only guard against unpaid L1-to-L2 messages: [1](#0-0) 

The `paid_fee_on_l1` value is meant to reflect what the L1 message sender actually paid on L1 (via the scraped `LogMessageToL2` event, which carries a `fee` field): [2](#0-1) 

However, `ConsensusTransaction::L1Handler` only carries the raw `transaction::L1HandlerTransaction` (no fee field at all), and `InternalConsensusTransaction::L1Handler` carries the executable form which does include `paid_fee_on_l1`: [3](#0-2) 

When a node converts a `ConsensusTransaction::L1Handler` (as received via consensus/p2p from a block proposal) back into the internal executable representation, it calls `convert_consensus_l1_handler_to_internal_l1_handler`, which **always substitutes a hardcoded `Fee(1)`** regardless of what was truly paid on L1: [4](#0-3) 

The TODO comment itself acknowledges this is a stand-in ("Change this once we put real value in paid_fee_on_l1"), confirming the omission is unintentional, not a deliberate design choice.

Because `execute_raw`'s only check is `paid_fee == Fee(0)` (any nonzero value, including the placeholder `1`, passes), this specific hardcoded value happens not to trigger `InsufficientFee` today — but it means the check is permanently neutralized for anyone reconstructing the transaction via the consensus path: the real amount paid on L1 is discarded and replaced by a constant that always satisfies the check. Any L1 message sender can thus have their message accepted and executed on L2 through the consensus/replay path regardless of the amount they actually paid on L1 (including effectively 0, since the scraped real fee is never consulted here), while other code paths (e.g. `test_utils`/blockifier tests, `native_blockifier`, `apollo_rpc`) that do carry and check the *real* `paid_fee_on_l1` would behave differently for the exact same underlying L1 message.

### Impact Explanation
This directly maps to the report's impact category: bypassing a value-based admission/entry check by substituting a synthetic zero/placeholder value instead of the real one. Concretely:
- Any node that reconstructs an `L1Handler` from `ConsensusTransaction` (validator replay, historical re-execution, syncing) computes `paid_fee_on_l1 = Fee(1)` unconditionally, so the "did the sender pay anything on L1" check is meaningless on this path — it can never fail due to `Fee(0)`, no matter what (if anything) was truly consumed on L1.
- Because the proposer's original executable transaction (built from the real L1 scraper data) could carry a different `paid_fee_on_l1` than what other honest nodes reconstruct via consensus, nodes following different code paths for the same logical L1 message could reach different fee-sufficiency conclusions, which is a form of state/receipt divergence for the same L1Handler transaction, undermining the fee-payment invariant that L1 handler execution is supposed to enforce network-wide.
- Since `paid_fee_on_l1` is not part of the transaction hash or committed L1Handler receipt fee validation beyond this single boolean check, this weakens the sequencer's ability to reject or account for unpaid/underpaid L1 messages uniformly, which the recommendation in the original report also targeted (per-item value should be validated against the real value, not skipped/faked).

### Likelihood Explanation
This code path executes on every `L1Handler` transaction that arrives via the consensus (`ConsensusTransaction`) representation rather than being built fresh by the local L1 event scraper — i.e., for any node that is not the original proposer of the block containing that L1Handler transaction (validators, syncing/late-joining nodes, any node reconstructing transactions from a received block/proposal). This is a normal, frequent code path (every L1Handler transaction, on every non-proposer node), not a rare edge case, and requires no privileged access — only that an L1 message (something any L1 account can send by calling the StarknetCore contract) exists in a block.

### Recommendation
Propagate the real `paid_fee_on_l1` value through the consensus representation instead of discarding it:
- Add the real fee (as scraped from the L1 `LogMessageToL2` event) to `ConsensusTransaction::L1Handler`, or otherwise ensure the value used in `convert_consensus_l1_handler_to_internal_l1_handler` is sourced from the same L1 event data the proposer used, not a hardcoded constant.
- Until fixed, treat `Fee(1)` as a known correctness gap: audit whether any consensus safety property depends on `paid_fee_on_l1` being accurate, and resolve the linked TODO before relying on this check for L1 handler fee sufficiency across all nodes.

### Proof of Concept
Not directly exploitable to steal funds/mint entries as in the original YoloV2 report, but the analog is demonstrated by direct code inspection:
1. An L1 message sender sends an L1-to-L2 message via StarknetCore paying some `fee` (possibly the actual fee amount is irrelevant since it's discarded).
2. A proposer builds a block including this `L1Handler` transaction; other nodes receive it as `ConsensusTransaction::L1Handler(tx)` (no fee field).
3. On any such node, `convert_consensus_tx_to_internal_consensus_tx` → `convert_consensus_l1_handler_to_internal_l1_handler` sets `paid_fee_on_l1 = Fee(1)` unconditionally: [4](#0-3) 
4. When this reconstructed transaction is executed/re-executed, the check `if paid_fee == Fee(0)` in `execute_raw` always passes (since `paid_fee` is hardcoded to `1`), regardless of what was truly paid on L1: [1](#0-0) 
5. This shows that the fee-sufficiency gate is not actually validating the real, sender-controlled input on this code path — the analogous "0 deposit" bypass from the original report.

### Citations

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

**File:** crates/apollo_l1_events/tests/flow_test_consumed.rs (L56-62)
```rust
    // We will first send this message.
    let message_to_l2_event = L1Event::LogMessageToL2 {
        tx: l1_handler_tx.clone(),
        fee: Fee::default(),
        l1_tx_hash: None,
        block_timestamp: block_timestamp(fake_clock.clone(), 0),
    };
```

**File:** crates/starknet_api/src/consensus_transaction.rs (L8-18)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum InternalConsensusTransaction {
    RpcTransaction(InternalRpcTransaction),
    L1Handler(executable_transaction::L1HandlerTransaction),
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
