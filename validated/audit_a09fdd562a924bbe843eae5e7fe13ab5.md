## Title
Stale cross-block shard outputs consumed after early `?` return in `LocalExecutorClient::get_output_from_shards` - (File: aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs)

## Summary
`LocalExecutorClient::get_output_from_shards` reads shard results sequentially from per-shard `crossbeam_channel::Receiver`s and propagates a `VMStatus` error via `?` the moment any shard's channel yields an `Err`. Because the loop is not exhaustive (it stops at the first error), any shard whose message has not yet been dequeued at that point is left un-consumed in its channel. Since the `LocalExecutorClient`/its shard threads and channels are long-lived (e.g. the `SHARDED_BLOCK_EXECUTOR` singleton in `execution/executor-service/src/local_executor_helper.rs`), the next call to `execute_block` will enqueue a new result behind the stale, unread one in that shard's FIFO channel, so the next `get_output_from_shards` call reads the leftover result from the *previous* (failed) block instead of the current one.

## Finding Description
`LocalExecutorClient::execute_block` [1](#0-0)  dispatches per-shard commands over `command_txs[i]` and then calls `get_output_from_shards`, which loops over `self.result_rxs` in shard-index order and does:

```
results.push(rx.recv().unwrap_or_else(...)?);
``` [2](#0-1) 

If shard `i` (0-indexed, `i < num_shards-1`) sends `Err(VMStatus)` while shards `i+1..n` are still computing or have already sent an `Ok` result that has not yet been consumed by this loop, the `?` fires right after `rx.recv()` for shard `i`, and the function returns immediately — the messages that shards `> i` already placed on their own dedicated channels are simply left sitting there, un-drained.

Each shard's `LocalExecutorService` runs a persistent loop (`ShardedExecutorService::start`) that keeps running and keeps sending results to the very same `result_tx` after any block, whether the previous block errored or not [3](#0-2) . The `LocalExecutorClient` (and its channels) is not recreated per block; in production it is held in a long-lived singleton, e.g. `SHARDED_BLOCK_EXECUTOR` [4](#0-3) .

Consequently, on the *next* `execute_block` call, `crossbeam_channel`'s FIFO ordering means `rx.recv()` for the affected shard(s) will return the stale leftover `Ok(Vec<Vec<TransactionOutput>>)` from the previous (failed) block rather than the freshly computed output for the current block. That stale value gets pushed into the new call's `sharded_output`/`ShardedExecutionOutput` and is subsequently aggregated by `ShardedBlockExecutor::execute_block` into `aggregated_results` [5](#0-4)  as if it were the current block's transaction outputs.

## Impact Explanation
If triggered, a validator's execution pipeline would silently splice `TransactionOutput`s belonging to an earlier, already-abandoned block execution into the output of the current block for one or more shards, producing an incorrect write set / event list / gas usage for that block. If any node/replica hits this differently (e.g. only nodes with a lagging error history), this is a state-divergence bug that could corrupt the locally computed ledger state and downstream proofs (write sets feeding into the state tree, transaction accumulator, etc.) — a genuine proof/storage-integrity concern per the review's impact criteria.

However, this bug is confined to the **sharded execution path** (`LocalExecutorClient` / `ShardedBlockExecutor`), which is used for the executor-as-a-service / remote-executor / benchmarking configurations, not the default single-node BlockSTM path (`AptosVMBlockExecutor::execute_block`) that mainnet validators use for normal consensus execution. I could not find, within the indexed code, evidence that `execute_block_sharded` is wired into the default mainnet consensus execution pipeline (it appears in test harnesses, benchmarks, `executor-service`, and the experimental `ptx-executor`).

## Likelihood Explanation
Triggering the precondition requires making the **shard-level VM execution** itself return `Err(VMStatus)` (not just discard a transaction) for one shard while other shards succeed. Under standard `AptosVM` block execution, transaction-level failures are represented as discarded/kept status inside `TransactionOutput`, not as an outer `VMStatus` `Err` from `execute_block`; an outer `Err` is normally reserved for VM-internal invariant violations, config errors, or fatal states — conditions not straightforwardly reachable purely by "transaction content" alone under the stated unprivileged-input scope. I was not able to fully confirm, within the available indexed code, a concrete unprivileged-transaction path that deterministically forces exactly one shard's per-block VM execution to return `Err` while sibling shards succeed (this would need deeper inspection of `BlockAptosVM`/block-executor internals, which is outside what I could verify with the tools/index available here).

## Recommendation
- Make `get_output_from_shards` unconditionally drain all `result_rxs` (e.g., collect all `recv()` results first, then apply `?`/error propagation), so no channel is left with an unread message after an error.
- Alternatively, on any shard error, explicitly drain/flush all other channels for the affected block round before returning, or recreate the shard channels for a fresh state after any execution error, ensuring channel state can never leak across `execute_block` invocations.
- Add a debug assertion/sequence-number tag to each shard result so a consuming call can detect and reject stale (out-of-sequence) block results defensively.

## Proof of Concept
Conceptually reproducible with the existing sharded-executor test harness:
1. Configure `LocalExecutorClient` with `num_shards >= 2`.
2. Craft/inject a scenario where shard 0 (or any non-last shard) returns `Err(VMStatus)` from its `ShardedExecutorService::execute_block` for block A, while shard 1 (a later shard) completes and sends `Ok(...)` for block A before the coordinator's loop reaches it.
3. Call `execute_block` for block A — observe it returns `Err`, and shard 1's `Ok` result for block A is left unconsumed in `result_rxs[1]`.
4. Immediately call `execute_block` again for block B with different transactions assigned to shard 1.
5. In `get_output_from_shards` for block B, assert that `results[1]` — expected to be shard 1's block-B output — is instead the leftover block-A output (mismatched transaction count/content), proving stale-channel-result mixing across blocks.

I could not directly execute this PoC within the current environment (no execution/tooling access here); this is a structural/code-review-based finding whose exact triggerability under mainnet's default (non-sharded) validator execution path remains unconfirmed. If you want this validated empirically, a Devin session with full repo/test execution access would be required to run the `aptos-move/aptos-vm/tests/sharded_block_executor.rs` test harness with an injected shard failure and confirm cross-block leakage.

### Citations

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

**File:** aptos-move/aptos-vm/src/sharded_block_executor/local_executor_shard.rs (L183-223)
```rust
    fn execute_block(
        &self,
        state_view: Arc<S>,
        transactions: PartitionedTransactions,
        concurrency_level_per_shard: usize,
        onchain_config: BlockExecutorConfigFromOnchain,
    ) -> Result<ShardedExecutionOutput, VMStatus> {
        assert_eq!(transactions.num_shards(), self.num_shards());
        let (sub_blocks, global_txns) = transactions.into();
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
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_executor_service.rs (L214-254)
```rust
    pub fn start(&self) {
        trace!(
            "Shard starting, shard_id={}, num_shards={}.",
            self.shard_id,
            self.num_shards
        );
        let mut num_txns = 0;
        loop {
            let command = self.coordinator_client.receive_execute_command();
            match command {
                ExecutorShardCommand::ExecuteSubBlocks(
                    state_view,
                    transactions,
                    concurrency_level_per_shard,
                    onchain_config,
                ) => {
                    num_txns += transactions.num_txns();
                    trace!(
                        "Shard {} received ExecuteBlock command of block size {} ",
                        self.shard_id,
                        num_txns
                    );
                    let exe_timer = SHARDED_EXECUTOR_SERVICE_SECONDS
                        .timer_with(&[&self.shard_id.to_string(), "execute_block"]);
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

**File:** execution/executor-service/src/local_executor_helper.rs (L14-21)
```rust
pub static SHARDED_BLOCK_EXECUTOR: Lazy<
    Arc<Mutex<ShardedBlockExecutor<CachedStateView, LocalExecutorClient<CachedStateView>>>>,
> = Lazy::new(|| {
    info!("LOCAL_SHARDED_BLOCK_EXECUTOR created");
    Arc::new(Mutex::new(
        LocalExecutorClient::create_local_sharded_block_executor(AptosVM::get_num_shards(), None),
    ))
});
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/mod.rs (L86-115)
```rust
        let (sharded_output, global_output) = self
            .executor_client
            .execute_block(
                state_view,
                transactions,
                concurrency_level_per_shard,
                onchain_config,
            )?
            .into_inner();
        // wait for all remote executors to send the result back and append them in order by shard id
        info!("ShardedBlockExecutor Received all results");
        let _aggregation_timer = SHARDED_EXECUTION_RESULT_AGGREGATION_SECONDS.start_timer();
        let num_rounds = sharded_output[0].len();
        let mut aggregated_results = vec![];
        let mut ordered_results = vec![vec![]; num_executor_shards * num_rounds];
        // Append the output from individual shards in the round order
        for (shard_id, results_from_shard) in sharded_output.into_iter().enumerate() {
            for (round, result) in results_from_shard.into_iter().enumerate() {
                ordered_results[round * num_executor_shards + shard_id] = result;
            }
        }

        for result in ordered_results.into_iter() {
            aggregated_results.extend(result);
        }

        // Lastly append the global output
        aggregated_results.extend(global_output);

        Ok(aggregated_results)
```
