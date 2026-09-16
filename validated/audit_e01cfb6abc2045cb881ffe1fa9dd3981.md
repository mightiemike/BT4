### Title
Panics in the batcher's block-building task are silently swallowed instead of crashing the server, masking a transaction-triggered liveness failure - (File: `crates/apollo_batcher/src/batcher.rs`)

### Summary
The `Batcher::spawn_proposal` function spawns the block-execution work (`block_builder.build_block()`, which executes attacker-submitted transactions) as a detached `tokio::spawn` task, and `await_active_proposal` joins on that handle with `let _ = tokio::join!(execution_join_handle, writer_future);`, discarding any `JoinError`. This is the same bug class as the referenced Penumbra summonerd fix: a contribution/worker task crash should be bubbled up and treated as fatal, not silently dropped.

### Finding Description
`spawn_proposal` spawns the execution task that calls `block_builder.build_block()` on a set of mempool/L1 transactions supplied to the batcher: [1](#0-0) 

The result is only recorded into `executed_proposals` if the task runs to completion; if `build_block()` panics (e.g. due to a crafted transaction hitting an unguarded panic path deep in execution), the `insert` into `executed_proposals` never happens, and the spawned task simply terminates with a `JoinError` that Tokio captures.

`await_active_proposal` then awaits this handle but explicitly discards the result: [2](#0-1) 

Compare this to the pattern the same codebase uses elsewhere specifically to avoid this exact class of bug — a comment even documents the danger of dropping a `JoinHandle` without checking it: [3](#0-2) 
and the dedicated `spawn_with_exit_on_panic` helper that terminates the process on an unexpected task panic: [4](#0-3) 

`await_active_proposal`'s execution path does not use this pattern, so a panic in the execution task is invisible to the batcher. The caller (`decision_reached`) subsequently looks the proposal up in `executed_proposals` and only surfaces a generic `BatcherError::ExecutedProposalNotFound`: [5](#0-4) 

Because the panic is never bubbled up (unlike the intended fail-fast behavior elsewhere in this codebase), the node does not crash, does not alert operators via a process exit, and does not distinguish "the executor crashed on this transaction" from an ordinary consensus race. Since block execution is deterministic, a single crafted transaction that triggers a panic during `build_block()` will panic identically on every honest validator that attempts to include it as proposer, and since the mempool has no signal to evict it (the batcher never surfaces that a panic — as opposed to a normal proposal failure — occurred), the poisoned transaction can keep being re-selected across leader rotations, repeatedly stalling block production at that height.

### Impact Explanation
If reachable, this allows an unprivileged transaction sender to submit a single transaction that deterministically panics the batcher's execution task on every node that tries to build a block containing it. Because the panic is silently absorbed rather than crashing the process (which would at least force operator intervention/restart with visibility into the root cause), the network can be left unable to reliably confirm new transactions at that height, a liveness failure across honest nodes.

### Likelihood Explanation
Requires finding/crafting a transaction that reaches an unguarded panic path inside the deterministic `block_builder.build_block()` / blockifier execution pipeline reachable via `Batcher::propose_block`/`validate_block`. Such execution-time panics on attacker-influenced input are a known general risk class in Rust-based deterministic execution engines; this report only establishes that the batcher lacks the fail-fast safety net that the rest of the codebase deliberately implements for this exact scenario, not a specific triggering payload.

### Recommendation
In `await_active_proposal` (and any other place awaiting `execution_join_handle`/`writer_join_handle`), do not discard the `Result` from `tokio::join!`. Match the pattern already used in `sequencer_consensus_context.rs`/`apollo_infra_utils::tasks::spawn_with_exit_on_panic`: on `Err(JoinError)` (panic), log with full diagnostics and terminate/crash the batcher process (or otherwise propagate a distinguished, non-swallowed error) instead of allowing normal control flow to continue past a swallowed panic.

### Proof of Concept
1. Craft a transaction whose deterministic execution triggers a panic inside `block_builder.build_block()`.
2. Submit it via the gateway so it lands in the mempool and gets included as a candidate transaction in `propose_block`/`validate_block`.
3. `spawn_proposal`'s execution task panics; `await_active_proposal`'s `let _ = tokio::join!(...)` discards the `JoinError`; `executed_proposals` is never populated for that `proposal_id`.
4. `decision_reached` returns `BatcherError::ExecutedProposalNotFound`, and the batcher process keeps running without ever recording/crash-signaling the panic.
5. Because execution is deterministic, every validator that becomes proposer and re-includes the same transaction from the mempool hits the identical panic, repeatedly failing to finalize a proposal at that height.

### Citations

**File:** crates/apollo_batcher/src/batcher.rs (L1019-1028)
```rust
    pub async fn decision_reached(
        &mut self,
        input: DecisionReachedInput,
    ) -> BatcherResult<DecisionReachedResponse> {
        let height = self.active_height.ok_or(BatcherError::NoActiveHeight)?;

        let proposal_id = input.proposal_id;
        let proposal_result = self.executed_proposals.lock().await.remove(&proposal_id);
        let block_execution_artifacts = proposal_result
            .ok_or(BatcherError::ExecutedProposalNotFound { proposal_id })?
```

**File:** crates/apollo_batcher/src/batcher.rs (L1272-1305)
```rust
        let execution_join_handle = tokio::spawn(
            async move {
                let result = match block_builder.build_block().await {
                    Ok(artifacts) => {
                        proposal_metrics_handle.set_succeeded();
                        Ok(artifacts)
                    }
                    Err(BlockBuilderError::Aborted) => {
                        proposal_metrics_handle.set_aborted();
                        Err(BlockBuilderError::Aborted)
                    }
                    Err(e) => Err(e),
                }
                .map_err(Arc::new);

                // The proposal is done, clear the active proposal.
                // Keep the proposal result only if it is the same as the active proposal.
                // The active proposal might have changed if this proposal was aborted.
                let mut active_proposal = active_proposal.lock().await;
                if *active_proposal == Some(proposal_id) {
                    active_proposal.take();

                    log_txs_execution_result(proposal_id, &result);

                    let proposal_already_exists =
                        executed_proposals.lock().await.insert(proposal_id, result);
                    assert!(
                        proposal_already_exists.is_none(),
                        "Duplicate proposal: {proposal_id}."
                    );
                }
            }
            .in_current_span(),
        );
```

**File:** crates/apollo_batcher/src/batcher.rs (L1395-1424)
```rust
    pub async fn await_active_proposal(
        &mut self,
        final_n_executed_txs: usize,
    ) -> BatcherResult<()> {
        if let Some(ProposalTask {
            execution_join_handle,
            writer_join_handle,
            final_n_executed_txs_sender,
            ..
        }) = self.active_proposal_task.take()
        {
            if let Some(final_n_executed_txs_sender) = final_n_executed_txs_sender {
                final_n_executed_txs_sender.send(final_n_executed_txs).map_err(|err| {
                    error!(
                        "Failed to send final_n_executed_txs ({final_n_executed_txs}) to the tx \
                         provider: {}",
                        err
                    );
                    BatcherError::InternalError
                })?;
            }

            let writer_future = writer_join_handle
                .map(FutureExt::boxed)
                .unwrap_or_else(|| futures::future::ready(Ok(())).boxed());
            let _ = tokio::join!(execution_join_handle, writer_future);
        }

        Ok(())
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L928-934)
```rust
        // Spawn a follow-up task to detect panics. Without this, if the reproposal
        // task panics, the JoinError is silently swallowed when the handle is dropped.
        tokio::spawn(async move {
            if let Err(e) = handle.await {
                error!("Reproposal task panicked: {e:?}");
            }
        });
```

**File:** crates/apollo_infra_utils/src/tasks.rs (L10-58)
```rust
/// Spawns a monitored asynchronous task in Tokio.
///
/// This function spawns two tasks:
/// 1. The first task executes the provided future.
/// 2. The second task awaits the completion of the first task.
///    - If the first task completes successfully, then it returns its result.
///    - If the first task panics, it logs the error and terminates the process with exit code 1.
///
/// # Type Parameters
///
/// - `F`: The type of the future to be executed. Must implement `Future` and be `Send + 'static`.
/// - `T`: The output type of the future. Must be `Send + 'static`.
///
/// # Arguments
///
/// - `future`: The future to be executed by the spawned task.
///
/// # Returns
///
/// A `JoinHandle<T>` of the second monitoring task.
pub fn spawn_with_exit_on_panic<F, T>(future: F) -> JoinHandle<T>
where
    F: Future<Output = T> + Send + 'static,
    T: Send + 'static,
{
    inner_spawn_with_exit_on_panic(future, exit_process)
}

// Use an inner function to enable injecting the exit function for testing.
pub(crate) fn inner_spawn_with_exit_on_panic<F, E, T>(future: F, on_exit_f: E) -> JoinHandle<T>
where
    F: Future<Output = T> + Send + 'static,
    E: FnOnce() + Send + 'static,
    T: Send + 'static,
{
    // Spawn the first task to execute the future
    let monitored_task = tokio::spawn(future);

    // Spawn the second task to await the first task and assert its completion
    tokio::spawn(async move {
        match monitored_task.await {
            Ok(res) => res,
            Err(err) => {
                error!("Monitored task failed: {:?}", err);
                on_exit_f();
                unreachable!()
            }
        }
    })
```
