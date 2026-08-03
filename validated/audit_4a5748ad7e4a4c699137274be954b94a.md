## Finding: Confirmed — the guarantee does NOT hold

The claim in the question is correct: `Drop::drop` for `LocalExecutorClient` provides no protection here, and the real defect is upstream in `execute_block` itself, which can return early without draining shard result channels, letting a stale `TransactionOutput` batch from an aborted block bleed into the *next* `execute_block` call's result stream.

### Root cause

`LocalExecutorClient::execute_block` first fans out `ExecutorShardCommand::ExecuteSubBlocks` to every shard's command channel, then computes the global-round output, and only *afterwards* drains the per-shard result channels: [1](#0-0) 

The `?` on `self.global_executor.execute_global_txns(...)` (line 211) causes an early `return Err(...)` **before** `self.get_output_from_shards()` (line 213) is ever called: [2](#0-1) 

Meanwhile, each shard thread runs independently and **unconditionally** sends its result once its `execute_block` finishes, regardless of whether the outer `LocalExecutorClient::execute_block` call ultimately succeeds or errors: [3](#0-2) 

Because `result_tx`/`result_rx` are unbounded `crossbeam_channel`s created once at `setup_local_executor_shards` and reused for the lifetime of the `LocalExecutorClient` (not per-call), any result produced by a shard for a call that the coordinator abandoned (via the `?` early-return) stays queued in FIFO order: [4](#0-3) [5](#0-4) 

On the very next `execute_block` call on the *same, non-dropped* `LocalExecutorClient` instance (which is the normal case — `ShardedBlockExecutor` is a long-lived object passed by reference across blocks, e.g. via `AptosVMBlockExecutor::execute_block_sharded`), `get_output_from_shards()` will `recv()` the stale, previously-queued result first, since channel order follows command dequeue order, not call identity: [6](#0-5) 

`Drop::drop` (lines 228-238) only tears down the shards and their channels when the `LocalExecutorClient` object itself is dropped/destroyed — it does nothing to protect against reuse of the *same* object across a failed call followed by a fresh call, which is exactly the scenario the exploit hypothesis targets: [7](#0-6) 

### Title
Stale shard `TransactionOutput` misbinding after partial `execute_block` failure in `LocalExecutorClient` (sharded VM executor) - (File: `aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs`)

### Summary
`LocalExecutorClient::execute_block` sends per-shard execution commands, then computes the global round, and only drains per-shard results after the global round succeeds. If the global round errors, the function returns early via `?` without calling `get_output_from_shards()`, leaving already-computed (or soon-to-arrive) shard results for the aborted block queued in the unbounded `result_rx` channels. Because the `LocalExecutorClient`/channels persist across block boundaries (they are not per-block, and `Drop` only runs on full teardown), the next `execute_block()` invocation dequeues the stale results first and binds them to the new block, mismatching them against the newly computed global-round output.

### Finding Description
`execute_block` (`local_executor_shard.rs:183-223`) is not resilient to the ordering assumption it makes: it assumes exactly one set of shard results is produced and consumed per call. That invariant breaks the moment the global-round call errors, because shard threads (`sharded_executor_service.rs:214-259`) always `send_execution_result` once they finish, independent of the coordinator's control flow. The unbounded `crossbeam_channel` used for `result_rx` (`local_executor_shard.rs:88-91`) has no per-call tagging/versioning, so there is no way for a later `get_output_from_shards()` call to detect that it is reading a result meant for a previous, aborted block.

### Impact Explanation
This corrupts the executor-to-storage handoff invariant explicitly protected by scope: "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged." A subsequent block would be committed with `TransactionOutput`s (write sets, events, gas usage, status) belonging to a different set of transactions than the ones actually specified for that block height/version, while the global-round output is correctly computed for the new block. This produces an internally inconsistent, wrongly-bound result set that — if committed — corrupts the state at that version and diverges from the deterministic result that non-sharded nodes (or sharded nodes that didn't hit the error) would compute, creating hard-fork-only divergence between sharded-execution nodes.

### Likelihood Explanation
This requires the sharded local executor path to be in use (validator/execution-service configured with `num_shards > 1`) and a genuine VM/global-round error (e.g., `VMStatus` invariant-violation class errors, which can originate from unusual but syntactically valid transaction/bytecode inputs during speculative or cross-shard execution) to occur mid-block. It is not a routine occurrence, but it does not require a malicious/trusted operator mistake — a legitimate transaction that triggers a VM-level error in the global round is sufficient to desynchronize the channel, and no additional privileged action is needed to trigger the misbinding on the following block.

### Recommendation
- Make `get_output_from_shards()` unconditionally executed (e.g., via a scope guard / `finally`-style drain) whenever `ExecuteSubBlocks` commands were sent, regardless of whether the global round errored, so channels are always fully drained per call.
- Tag each `ExecutorShardCommand`/result with a monotonically increasing block/call id and have `get_output_from_shards` validate the id before accepting a result, rejecting/discarding any stale results.
- Alternatively, recreate the `LocalExecutorClient` (full teardown via `Drop` + `setup_local_executor_shards`) on any `execute_block` error path so that the channels are guaranteed to be fresh for the next block.

### Proof of Concept
1. Configure `LocalExecutorClient` with `num_shards >= 1` such that `sub_blocks` is non-empty.
2. Craft/force `global_executor.execute_global_txns` to return `Err(VMStatus)` for block N (e.g., via the existing `fail_point!("aptos_vm_block_executor::execute_block_with_config", ...)` hook or an invariant-violation-inducing transaction in the global round) while at least one shard's `ExecuteSubBlocks` for block N completes successfully and calls `send_execution_result`.
3. Observe `execute_block` for block N returns `Err` at line 211, without calling `get_output_from_shards()` — the shard's `Ok(...)` result for block N remains in `result_rx`.
4. Call `execute_block` again for block N+1 with different transactions/sub-blocks.
5. Observe that `get_output_from_shards()` (line 213) for the block N+1 call returns the shard's stale block-N `Vec<Vec<TransactionOutput>>` (not block N+1's), which then gets aggregated with block N+1's freshly-computed global output in `ShardedBlockExecutor::execute_block` (`mod.rs:70-116`) and returned to the caller as if it were block N+1's result.

### Citations

**File:** aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs (L84-127)
```rust
        let (command_txs, command_rxs): (
            Vec<Sender<ExecutorShardCommand<S>>>,
            Vec<Receiver<ExecutorShardCommand<S>>>,
        ) = (0..num_shards).map(|_| unbounded()).unzip();
        let (result_txs, result_rxs): (
            Vec<Sender<Result<Vec<Vec<TransactionOutput>>, VMStatus>>>,
            Vec<Receiver<Result<Vec<Vec<TransactionOutput>>, VMStatus>>>,
        ) = (0..num_shards).map(|_| unbounded()).unzip();
        // We need to create channels for each shard and each round. This is needed because individual
        // shards might send cross shard messages to other shards that will be consumed in different rounds.
        // Having a single channel per shard will cause a shard to receiver messages that is not intended in the current round.
        let (cross_shard_msg_txs, cross_shard_msg_rxs): (
            Vec<Vec<Sender<CrossShardMsg>>>,
            Vec<Vec<Receiver<CrossShardMsg>>>,
        ) = (0..num_shards)
            .map(|_| {
                (0..MAX_ALLOWED_PARTITIONING_ROUNDS)
                    .map(|_| unbounded())
                    .unzip()
            })
            .unzip();
        let executor_shards = command_rxs
            .into_iter()
            .zip(result_txs)
            .zip(cross_shard_msg_rxs)
            .enumerate()
            .map(|(shard_id, ((command_rx, result_tx), cross_shard_rxs))| {
                let cross_shard_client = LocalCrossShardClient::new(
                    global_cross_shard_tx.clone(),
                    cross_shard_msg_txs.clone(),
                    cross_shard_rxs,
                );
                Self::new(
                    shard_id as ShardId,
                    num_shards,
                    num_threads,
                    command_rx,
                    result_tx,
                    cross_shard_client,
                )
            })
            .collect();
        LocalExecutorClient::new(command_txs, result_rxs, executor_shards, global_executor)
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs (L164-175)
```rust
    fn get_output_from_shards(&self) -> Result<Vec<Vec<Vec<TransactionOutput>>>, VMStatus> {
        let _timer = WAIT_FOR_SHARDED_OUTPUT_SECONDS.start_timer();
        trace!("LocalExecutorClient Waiting for results");
        let mut results = vec![];
        for (i, rx) in self.result_rxs.iter().enumerate() {
            results.push(
                rx.recv()
                    .unwrap_or_else(|_| panic!("Did not receive output from shard {}", i))?,
            );
        }
        Ok(results)
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs (L192-222)
```rust
        for (i, sub_blocks_for_shard) in sub_blocks.into_iter().enumerate() {
            self.command_txs[i]
                .send(ExecutorShardCommand::ExecuteSubBlocks(
                    state_view.clone(),
                    sub_blocks_for_shard,
                    concurrency_level_per_shard,
                    onchain_config,
                ))
                .unwrap();
        }

        // This means that we are executing the global transactions concurrently with the individual shards but the
        // global transactions will be blocked for cross shard transaction results. This hopefully will help with
        // finishing the global transactions faster but we need to evaluate if this causes thread contention. If it
        // does, then we can simply move this call to the end of the function.
        let mut global_output = self.global_executor.execute_global_txns(
            global_txns,
            state_view.as_ref(),
            onchain_config,
        )?;

        let mut sharded_output = self.get_output_from_shards()?;

        sharded_aggregator_service::aggregate_and_update_total_supply(
            &mut sharded_output,
            &mut global_output,
            state_view.as_ref(),
            self.global_executor.get_executor_thread_pool(),
        );

        Ok(ShardedExecutionOutput::new(sharded_output, global_output))
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs (L228-238)
```rust
impl<S: StateView + Sync + Send + 'static> Drop for LocalExecutorClient<S> {
    fn drop(&mut self) {
        for command_tx in self.command_txs.iter() {
            let _ = command_tx.send(ExecutorShardCommand::Stop);
        }

        // wait for join handles to finish
        for executor_service in self.executor_services.iter_mut() {
            let _ = executor_service.join_handle.take().unwrap().join();
        }
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_executor_service.rs (L238-254)
```rust
                    let ret = self.execute_block(
                        transactions,
                        state_view.as_ref(),
                        BlockExecutorConfig {
                            local: BlockExecutorLocalConfig::default_with_concurrency_level(
                                concurrency_level_per_shard,
                            ),
                            onchain: onchain_config,
                        },
                    );
                    drop(state_view);
                    drop(exe_timer);

                    let _result_tx_timer = SHARDED_EXECUTOR_SERVICE_SECONDS
                        .timer_with(&[&self.shard_id.to_string(), "result_tx"]);
                    self.coordinator_client.send_execution_result(ret);
                },
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L3450-3476)
```rust
    fn execute_block_sharded<S: StateView + Sync + Send + 'static, C: ExecutorClient<S>>(
        sharded_block_executor: &ShardedBlockExecutor<S, C>,
        transactions: PartitionedTransactions,
        state_view: Arc<S>,
        onchain_config: BlockExecutorConfigFromOnchain,
    ) -> Result<Vec<TransactionOutput>, VMStatus> {
        let log_context = AdapterLogSchema::new(state_view.id(), 0);
        info!(
            log_context,
            "Executing block, transaction count: {}",
            transactions.num_txns()
        );

        let count = transactions.num_txns();
        let ret = sharded_block_executor.execute_block(
            state_view,
            transactions,
            AptosVM::get_concurrency_level(),
            onchain_config,
        );
        if ret.is_ok() {
            // Record the histogram count for transactions per block.
            BLOCK_TRANSACTION_COUNT.observe(count as f64);
        }
        ret
    }
}
```
