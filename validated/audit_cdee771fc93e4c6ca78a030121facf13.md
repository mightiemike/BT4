Confirmed: in `crates/apollo_batcher/src/block_builder.rs::collect_execution_results_and_stream_txs`, **every** `L1Handler` input transaction whose executor call returns `Ok(...)` — whether or not `tx_execution_info.revert_error.is_some()` — is unconditionally inserted into `execution_data.consumed_l1_handler_tx_hashes`, with only a warning log for the reverted case: [1](#0-0) 

This is corroborated by the test `failed_l1_handler_transaction_consumed`, whose name and assertion explicitly show that an L1 handler transaction that fails/reverts during execution (`Err(TransactionExecutorError::StateError(...))`/reverted result) still ends up in `consumed_l1_handler_tx_hashes`: [2](#0-1) 

This `consumed_l1_handler_tx_hashes` set is exactly what the batcher forwards to `commit_block` on the L1 events provider, which permanently marks those hashes as `Committed` in the `TransactionManager` (moving them out of the `Pending`/proposable pool forever): [3](#0-2) [4](#0-3) 

Meanwhile, on the Starknet OS (Cairo) side, `execute_l1_handler_transaction` only calls `consume_l1_to_l2_message` when `is_reverted == FALSE`; if the transaction is reverted, message consumption is skipped entirely and the function returns early: [5](#0-4) [6](#0-5) 

### Title
Sequencer marks reverted L1-handler transactions as "consumed", permanently orphaning the underlying L1→L2 message and its funds - (File: `crates/apollo_batcher/src/block_builder.rs`)

### Summary
Any L1-handler transaction that is included in a block by the sequencer is unconditionally added to `consumed_l1_handler_tx_hashes`, regardless of whether its execution reverted. This set is used to permanently mark the transaction as `Committed` in the L1 provider's `TransactionManager`, removing it from the pool of proposable/retryable L1 messages forever — even though the on-chain (Starknet OS) semantics for a reverted L1 handler explicitly skip the `consume_l1_to_l2_message` step, meaning the real message-nonce/consumption counter is never decremented for that message. The sequencer's bookkeeping and the OS's actual state-diff semantics diverge for reverted L1 handlers.

### Finding Description
`collect_execution_results_and_stream_txs` inserts the transaction hash of every `InternalConsensusTransaction::L1Handler` into `execution_data.consumed_l1_handler_tx_hashes` before even inspecting the execution result, and only logs a warning if `tx_execution_info.revert_error.is_some()` — it does not exclude reverted transactions from the "consumed" set (`crates/apollo_batcher/src/block_builder.rs:636-652`). This is confirmed by the batcher's own regression test `failed_l1_handler_transaction_consumed`, whose explicit purpose is to assert that a *failed*/reverted L1-handler transaction still lands in `consumed_l1_handler_tx_hashes` (`crates/apollo_batcher/src/block_builder_test.rs:1079-1127`).

That set is subsequently forwarded, unfiltered from revert status, to `L1EventsProviderClient::commit_block` (`crates/apollo_batcher/src/batcher.rs:1111-1121`), which calls `TransactionManager::commit_txs`, moving the transaction's record from `Pending` to `Committed` (`crates/apollo_l1_events/src/transaction_manager.rs:147-165`). Per the invariant comment on `records` ("keeps transactions until they can be safely removed, like when they are consumed on L1"), once marked `Committed`, the transaction is never proposed again.

However, the actual state-diff/consumption semantics computed by the Starknet OS for L1-handler transactions explicitly gate `consume_l1_to_l2_message` behind `is_reverted == FALSE`; for a reverted L1 handler, the OS returns early and never calls `consume_l1_to_l2_message`, so the real consumed-message counter/nonce is not decremented in the committed state diff. This is the correct, safe design on the OS side (the same fix pattern the external report recommends — don't finalize/consume on failure). But the batcher-side bookkeeping (`consumed_l1_handler_tx_hashes` → L1 provider `Committed` state) does not mirror this distinction: it treats "included in a block" as "consumed," even for reverted transactions.

The practical effect mirrors the referenced Velodrome WeVE bug: a user's L1→L2 message (e.g., a deposit/mint call) that reverts on L2 (due to a transient condition — insufficient target contract balance, a resource/fee-check failure, a race with other txs mutating shared state, etc.) is treated by the sequencer as permanently handled. The L1 provider will never re-propose it, and there is no other mechanism in this codebase for a user to force re-inclusion of the same L1 message once the provider's records mark it `Committed`. If the L2-side operation that would have credited the user's funds never executes (because it reverted), and the L1 event provider believes the message is finished, the value tied to that L1→L2 message can become permanently unreachable through the deposit path — the sequencer's local state has diverged from the true consumption state that would be reflected on L1.

### Impact Explanation
This causes honest-node/sequencer-state divergence from the true L1 message-consumption semantics and can result in permanent loss/freezing of user funds tied to L1→L2 messages that revert on the receiving (L2) side: the message is dropped from the retry pool by the sequencer bookkeeping even though it was never actually "consumed" per the OS's state diff, and no other mechanism resubmits it. This satisfies "concrete loss or permanent freezing of funds" from a single, unprivileged L1→L2 message.

### Likelihood Explanation
Reachable purely from an ordinary user's L1→L2 message: any legitimate cause of an L1-handler revert (fee/resource-check failure at `crates/blockifier/src/transaction/l1_handler_transaction.rs:117-129`, an entry-point execution error at lines `132-141`, or a target-contract-state condition causing revert) triggers this path without any operator/proposer misbehavior. Since L1 handler cooldown/proposal logic already anticipates transient failures and staging, a revert is a normal, expected occurrence, not a contrived edge case, making this readily reachable in production operation.

### Recommendation
Do not add a reverted L1-handler transaction's hash to `consumed_l1_handler_tx_hashes` in `collect_execution_results_and_stream_txs`; only insert hashes for L1-handler transactions whose `tx_execution_info.revert_error` is `None`, so the bookkeeping matches the OS's actual `consume_l1_to_l2_message` gating. Correspondingly, reverted L1-handler transactions should be treated like rejected ones (kept `Pending`/re-proposable) so the message can be retried or so an explicit user-facing refund/cancellation flow (already partly modeled by `MessageToL2CancellationStarted`/`MessageToL2Canceled`) can be used instead of a silent, permanent drop.

### Proof of Concept
1. A user sends an L1→L2 message (e.g., a deposit calling an L1-handler entry point that would mint/credit tokens on L2).
2. The L1 events provider scrapes and stages the `L1HandlerTransaction`; the batcher includes it in a proposed block.
3. During execution, the L1 handler transaction reverts (e.g., `FeeCheckReport` fails at `crates/blockifier/src/transaction/l1_handler_transaction.rs:117-129`, or the target contract call fails) — `tx_execution_info.revert_error` is `Some(...)`.
4. `collect_execution_results_and_stream_txs` (`crates/apollo_batcher/src/block_builder.rs:636-652`) still inserts the tx hash into `consumed_l1_handler_tx_hashes` (per the existing test `failed_l1_handler_transaction_consumed`).
5. `commit_proposal_and_block` forwards this hash to the L1 provider's `commit_block`, and `TransactionManager::commit_txs` marks the record `Committed` (`crates/apollo_l1_events/src/transaction_manager.rs:147-165`).
6. On the OS/state-diff side, because the transaction reverted, `consume_l1_to_l2_message` was never invoked, so the true consumption counter for that message was not decremented in the committed state diff.
7. The user's L1 message will never be re-proposed by the sequencer (its record is `Committed`), yet the intended L2-side credit never happened — the associated value is permanently unreachable through this deposit path.

### Citations

**File:** crates/apollo_batcher/src/block_builder.rs (L636-652)
```rust
        if let InternalConsensusTransaction::L1Handler(_) = input_tx {
            let is_new_entry = execution_data.consumed_l1_handler_tx_hashes.insert(tx_hash);
            // Even though this doesn't get past the set insertion, this indicates a major, possibly
            // reorg-producing bug, either in some batcher cache or the l1 provider.
            assert!(is_new_entry, "Duplicate L1 handler transaction hash: {tx_hash}.");
        }

        match result {
            Ok((tx_execution_info, state_maps)) => {
                if let Some(ref revert_error) = tx_execution_info.revert_error {
                    warn!(
                        "Transaction {} is reverted during execution while still accepted. Revert \
                         Error: {}",
                        input_tx.tx_hash(),
                        revert_error,
                    );
                }
```

**File:** crates/apollo_batcher/src/block_builder_test.rs (L1079-1127)
```rust
#[rstest]
#[tokio::test]
async fn failed_l1_handler_transaction_consumed() {
    let l1_handler_txs = test_l1_handler_txs(0..2);
    let mock_tx_provider = mock_tx_provider_small_stream(l1_handler_txs.clone());

    let mut helper = ExpectationHelper::new();
    helper.expect_successful_get_new_results(0);
    helper.expect_is_done(false);
    helper.expect_add_txs_to_block(&l1_handler_txs);
    helper.expect_get_new_results_with_results(vec![
        Err(TransactionExecutorError::StateError(StateError::OutOfRangeContractAddress)),
        Ok((execution_info(), StateMaps::default())),
    ]);
    helper.expect_is_done(true);
    helper.expect_successful_get_new_results(0);

    helper.mock_transaction_executor.expect_close_block().times(1).return_once(|_| {
        Ok(BlockExecutionSummary {
            state_diff: Default::default(),
            compressed_state_diff: None,
            #[cfg(feature = "os_input")]
            initial_reads: test_initial_reads(),
            bouncer_weights: BouncerWeights::empty(),
            casm_hash_computation_data_sierra_gas: CasmHashComputationData::default(),
            casm_hash_computation_data_proving_gas: CasmHashComputationData::default(),
            compiled_class_hashes_for_migration: vec![],
            block_info: BlockInfo::create_for_testing(),
        })
    });

    let (_abort_sender, abort_receiver) = tokio::sync::oneshot::channel();
    let result_block_artifacts = run_build_block(
        helper.mock_transaction_executor,
        mock_tx_provider,
        None,
        false,
        abort_receiver,
        BLOCK_GENERATION_DEADLINE_SECS,
        DEFAULT_IDLE_TIMEOUT_MS,
    )
    .await
    .unwrap();

    // Verify that all L1 handler transaction's are included in the consumed l1 transactions.
    assert_eq!(
        result_block_artifacts.execution_data.consumed_l1_handler_tx_hashes,
        l1_handler_txs.iter().map(|tx| tx.tx_hash()).collect::<IndexSet<_>>()
    );
```

**File:** crates/apollo_batcher/src/batcher.rs (L1111-1121)
```rust
        // Notify the L1 provider of the new block.
        let rejected_l1_handler_tx_hashes = rejected_tx_hashes
            .iter()
            .copied()
            .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
            .collect();

        let l1_events_provider_result = self
            .l1_events_provider_client
            .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
            .await;
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L147-165)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L383-390)
```text
    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-448)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );
```
