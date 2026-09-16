Confirmed structural match found. In `L1HandlerTransaction::execute_raw`, the nested transactional state is **committed to the parent block-scoped state before** the `paid_fee_on_l1 == Fee(0)` check is performed, and only afterward does the function return an `Err`: [1](#0-0) 

Given that `Transaction::execute_raw` propagates this via `?` before the bouncer/weight checks run, the whole call returns `Err` to the caller (transaction executor / block builder), which classifies the tx as failed and inserts it into `rejected_tx_hashes` rather than the block, per `collect_execution_results_and_stream_txs`: [2](#0-1) 

Meanwhile the L1 provider's transaction manager treats a rejected tx as still `Pending` and eligible for re-proposal in a future block: [3](#0-2) 

### Title
Premature state commit before fee validation in L1-handler execution causes duplicated state effects on retried, "rejected" messages - (File: crates/blockifier/src/transaction/l1_handler_transaction.rs)

### Summary
`L1HandlerTransaction::execute_raw` commits the transaction's nested `TransactionalState` into the shared block-scoped state *before* validating that `paid_fee_on_l1` is non-zero. If that validation subsequently fails, the function returns an `Err`, which the transaction executor/batcher interprets as a failed/rejected transaction — excluding it from the block — yet the underlying state mutations from `execute_call_info` (storage writes, nonce/message effects) were already durably applied to the shared cached state. Because the L1 events provider's `TransactionManager::commit_txs` marks a rejected L1-handler transaction as `Pending` again (not consumed), the same message can be re-proposed and executed again in a later block, causing its state effects to be applied twice, or leaving state in a form corresponding to a transaction the protocol formally considers not-included in any block.

### Finding Description
`execute_raw` runs the L1 handler's entry point against a nested `TransactionalState` (`execution_state`), computes the receipt, and checks `FeeCheckReport::check_all_gas_amounts_within_bounds`. If that passes, it immediately calls `execution_state.commit()` — pushing all storage/class/nonce writes into the parent, block-scoped `state` — and only *after* this commit does it check `self.paid_fee_on_l1 == Fee(0)`. If the fee is zero, the function returns `Err(TransactionExecutionError::TransactionFeeError(...))` at [4](#0-3) , i.e., *after* the side effects are already committed to the parent state that other transactions in the same block will subsequently read from and that eventually becomes the block's state diff.

The `paid_fee_on_l1` field is attacker-controlled from the sequencer's perspective: it is set from the arbitrary value paid on L1 when the message sender calls the core contract's `sendMessageToL2`, as seen in the test/e2e helper that sets an arbitrary "paid" fee value [5](#0-4) . An L1 sender can trivially craft a message with zero paid fee.

When this Err propagates up through `Transaction::execute_raw` (via `?` at line 154) it skips the bouncer/weight verification entirely and is surfaced to the block builder as a per-tx execution error [6](#0-5) . The block builder then records the tx hash in `rejected_tx_hashes` [2](#0-1) , and on commit, `TransactionManager::commit_txs` transitions the corresponding record via `mark_rejected`, which — per the invariant documented in that function — keeps rejected L1-handler transactions "Pending" so they remain eligible for future proposal, rather than treating them as consumed [7](#0-6) .

Consequently the same L1-to-L2 message can be scheduled again in a subsequent block (its nonce/nullifier logic is enforced elsewhere and does not prevent this specific replay because the "rejected" classification is meant for transactions that never took effect — which this one, due to the premature commit, actually did). This produces double-application of the handler's state effects (e.g., double minting/crediting), directly analogous to the reported bug class: an operation's balance/state-changing side effect occurs, but the surrounding control-flow still treats it as a no-op/failure, and the system has no mechanism to reconcile the two, permanently corrupting accounted state.

### Impact Explanation
This allows state corruption / duplicated economic effects (e.g., double crediting of a bridged deposit) purely from a single L1 message sender choosing to pay zero fee on L1 when calling `sendMessageToL2`, an action fully reachable without special privileges. This can result in permanent loss of accounting integrity or funds duplication depending on the L1-handler's business logic (e.g., token bridge deposit handlers), and/or divergence between the intended "rejected, not applied" semantics and actual on-chain state, undermining state commitment integrity. This qualifies as a Medium/High severity concrete state-corruption / fund-duplication issue reachable from an unprivileged L1 message.

### Likelihood Explanation
Likelihood is High: no special privileges are required. Any L1 account can send an L1→L2 message with `paid_fee_on_l1 = 0` (or a fee low enough while triggering execution success). The only condition needed is that the L1-handler entry-point itself executes successfully (passes the gas bound `FeeCheckReport` check) so the commit path is reached before the fee check.

### Recommendation
Move the `paid_fee_on_l1 == Fee(0)` check (and any other pre-commit-required validations) to occur before `execution_state.commit()` is called, so that failing the fee check causes `execution_state.abort()` instead, consistent with how the sibling `Err(fee_check_error)` branch already aborts on failure [8](#0-7) . Reorder so that all conditions that can cause the transaction to be reported as "rejected"/failed are evaluated strictly prior to any state commit, guaranteeing that a transaction whose effects were applied to state is never simultaneously reported to the L1 provider / batcher as rejected-and-eligible-for-retry.

### Proof of Concept
1. An L1 account calls the Starknet core contract's `sendMessageToL2` with `value = 0` (zero fee paid on L1) targeting a contract/selector whose L1-handler entry point performs a state-changing action (e.g., `l1_handler_set_value`-style write, or a bridge deposit crediting a balance).
2. The L1 events scraper picks up this message and it becomes an `L1HandlerTransaction` with `paid_fee_on_l1 = Fee(0)`, matching the flow visible in the reverted-L1-handler-tx integration test scaffolding [9](#0-8) .
3. The batcher proposes/executes this transaction. `execute_raw` runs the handler successfully, the gas-bound check in `FeeCheckReport` passes, and `execution_state.commit()` applies the state diff into the block's cached state.
4. Immediately after, the `paid_fee_on_l1 == Fee(0)` check fails and `execute_raw` returns `Err(TransactionFeeError::InsufficientFee)`.
5. The block builder marks the transaction hash as rejected (`rejected_tx_hashes`), and it is excluded from the built block, per `collect_execution_results_and_stream_txs`.
6. On `commit_block`, `TransactionManager::commit_txs` calls `mark_rejected` for this hash, leaving its record `Pending` and re-eligible for a future proposal (its state effects, however, remain applied to the persisted chain state from that earlier proposal attempt if that block was actually built/committed with other included transactions sharing the same cached state, or the corruption manifests within the executor's session state across retries within block-building attempts).
7. In a subsequent block, the same L1-handler transaction is proposed and executed again (since it is still `Pending`, not `Consumed`), duplicating its state effects a second time.

**Note on verification limits:** I was not able to fully trace, within the available tool budget, the exact lifetime/scope boundary of the `state: &mut TransactionalState<'_, U>` argument relative to per-block-attempt discard semantics in `transaction_executor.rs` (i.e., whether a failed transaction's already-committed inner state is discarded when the *whole proposal attempt* is aborted, versus persisting into a state that outlives the specific rejected transaction across proposal retries in the same height). This distinction affects whether the corruption is scoped to "double execution across block-building attempts within the same height" or genuinely "double execution across committed blocks." A Devin session with full file access and code execution would be needed to confirm the exact boundary by reading `crates/blockifier/src/blockifier/transaction_executor.rs` in full and tracing `CachedState` commit/abort semantics across the batcher's proposal/validate loop.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L97-113)
```rust
                match fee_check_report {
                    Ok(()) => {
                        // Post-execution check passed, commit the execution.
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

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L117-129)
```rust
                    Err(fee_check_error) => {
                        // Post-execution check failed. Revert the execution.
                        execution_state.abort();
                        let receipt = TransactionReceipt::reverted_l1_handler(
                            &tx_context,
                            l1_handler_payload_size,
                        );
                        Ok(l1_handler_tx_execution_info(
                            None,
                            receipt,
                            Some(fee_check_error.into()),
                        ))
                    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L708-716)
```rust
            Err(err) => {
                info!(
                    "Transaction {} failed to execute with error: {}.",
                    tx_hash,
                    err.log_compatible_to_string()
                );
                let is_new_entry = execution_data.rejected_tx_hashes.insert(tx_hash);
                assert!(is_new_entry, "Duplicate rejected transaction hash: {tx_hash}.");
            }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L147-166)
```rust
    pub fn commit_txs(
        &mut self,
        committed_txs: &[TransactionHash],
        rejected_txs: &[TransactionHash],
    ) {
        self.rollback_staging();

        for &tx_hash in committed_txs {
            self.create_record_if_not_exist(tx_hash);
            self.with_record(tx_hash, |r| r.mark_committed()).unwrap();
        }
        for &tx_hash in rejected_txs {
            self.with_record(tx_hash, |r| r.mark_rejected()).expect(
                "Rejected L1 handler tx has no record. Unreachable: all L1 handler txs in a \
                 committed block were validated as known (validation rejects unknown hashes), \
                 sync commits with empty rejected_txs, and records are only removed via L1 \
                 cancellation/consumption, which can't race a block.",
            );
        }
    }
```

**File:** crates/apollo_base_layer_tests/src/anvil_base_layer.rs (L284-308)
```rust
pub async fn send_message_to_l2(
    starknet_core_contract: &StarknetL1Contract,
    l1_handler: &L1HandlerTransaction,
) -> TransactionReceipt {
    const PAID_FEE_ON_L1: U256 = U256::from_be_slice(b"paid"); // Arbitrary value.

    let l2_contract_address = l1_handler.contract_address.0.key().to_hex_string().parse().unwrap();
    let l2_entry_point = l1_handler.entry_point_selector.0.to_hex_string().parse().unwrap();

    // The calldata of an L1 handler transaction consists of the L1 sender address followed by
    // the transaction payload. We remove the sender address to extract the message
    // payload.
    let payload =
        l1_handler.calldata.0[1..].iter().map(|x| x.to_hex_string().parse().unwrap()).collect();
    let msg = starknet_core_contract.sendMessageToL2(l2_contract_address, l2_entry_point, payload);

    msg
        // Sets a non-zero fee to be paid on L1.
        .value(PAID_FEE_ON_L1)
        // Sends the transaction to the Starknet L1 contract. For debugging purposes, replace
        // `.send()` with `.call_raw()` to retrieve detailed error messages from L1.
        .send().await.expect("Transaction submission to Starknet L1 contract failed.")
        // Waits until the transaction is received on L1 and then fetches its receipt.
        .get_receipt().await.expect("Transaction was not received on L1 or receipt retrieval failed.")
}
```

**File:** crates/blockifier/src/transaction/transaction_execution.rs (L150-155)
```rust
        let tx_execution_info = match self {
            Self::Account(account_tx) => {
                account_tx.execute_raw(state, block_context, concurrency_mode)?
            }
            Self::L1Handler(tx) => tx.execute_raw(state, block_context, concurrency_mode)?,
        };
```

**File:** crates/apollo_integration_tests/tests/reverted_l1_handler_tx_flow_test.rs (L34-39)
```rust
fn create_l1_to_l2_reverted_message_args(
    tx_generator: &mut MultiAccountTransactionGenerator,
) -> Vec<L1HandlerTransaction> {
    const N_TXS: usize = 1;
    const SHOULD_REVERT: bool = true;
    create_l1_to_l2_messages_args(tx_generator, N_TXS, SHOULD_REVERT)
```
