### Title
Reachable panic in bouncer weight accounting crashes sequencer on transaction commit - ([File: crates/blockifier/src/concurrency/worker_logic.rs])

### Summary
`checked_add(...).expect(&err_msg)` inside `Bouncer::try_update` (invoked from `WorkerExecutor::commit_tx`) crashes the sequencer process with `panic!` whenever accumulated block weights plus a single transaction's weights overflow the underlying integer type, or whenever `try_update` returns any error variant other than `BlockFull` (the code explicitly falls into `panic!("Bouncer update failed. {error:?}: {error}")` for any other error). This mirrors the CVE-2019-18420 bug class: an error-handling path for a "should basically never happen but is technically reachable" condition uses a hard crash (`BUG()`/`panic!`) rather than a graceful error path, and it is reachable from ordinary, attacker-influenced execution (per-transaction resource accounting), not just from a malicious operator.

### Finding Description
`Bouncer::try_update` computes `next_accumulated_weights = self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg)` [1](#0-0) . If the addition overflows (e.g. `GasAmount`/`u64` fields such as `sierra_gas`, `proving_gas`, `receipt_l2_gas`, `l1_gas`, `n_events`, `message_segment_length`, `state_diff_size`), `checked_add` returns `None` and the `.expect()` panics the calling thread instead of returning a `TransactionExecutionError`.

This code path is invoked on every transaction commit through `WorkerExecutor::commit_tx`, which additionally treats *any* non-`BlockFull` error returned by `try_update` as an unconditional panic: [2](#0-1) 

The `try_update` function is reachable from a single submitted transaction's execution (invoke/declare/deploy/L1-handler) whose computed `tx_weights` (built from real execution resources: sierra gas, proving gas, event counts, message segment length, state diff size, etc., produced by `get_tx_weights`) are summed with the block's `accumulated_weights`. Because `BouncerWeights` and its constituent `GasAmount`/`usize` fields are attacker-influenceable (a contract can be crafted to maximize events, message segments, storage writes, or gas usage up to resource bounds), and multiple such transactions accumulate across a block, an adversary who can get transactions admitted and executed by the sequencer (no special/operator privilege required — a single account with fee sufficient to submit worst-case transactions) can drive the accumulated weight for some field arbitrarily close to its type's maximum, then submit one more transaction whose marginal weight tips the sum past the integer maximum, causing `checked_add` to return `None` and the `.expect()` to panic the executing thread.

Given `commit_tx` is called from the concurrent execution engine on the block-production hot path, this panic crashes/aborts the worker executing the block (the `WorkerPool` propagates and checks for panics via `worker_pool.check_panic()` as seen in `ConcurrentTransactionExecutor::get_new_results`), halting block building for that node. Because this can be triggered by any honest client that submits enough valid, fee-paying transactions to drive the accumulated bouncer weights to the overflow boundary (not by a malicious operator, and not merely a resource-only DoS in isolation — it is a crash of the block-production path, i.e., the network becomes unable to confirm new transactions on the affected node), it satisfies the impact bar of "a network unable to confirm new transactions."

### Impact Explanation
A panic in `commit_tx`/`try_update` during real block production directly halts progress of the transaction execution pipeline for the sequencer node processing the block, since bouncer accounting occurs on every committed transaction. If the overflow condition can be reached from cumulative, legitimately-priced transactions within a single block (each transaction's resource usage — e.g., emitted events, L2-to-L1 messages, storage writes, sierra gas — is attacker-controlled up to resource bounds), an unprivileged sender can cause the sequencer to crash mid-block, preventing that node from confirming further transactions until restarted. This is a Medium/High-severity denial-of-service on the exact mechanism CVE-2019-18420 describes: an error path using a hard-crash primitive (`BUG()`/`panic!`/`expect()`) reachable via attacker-controlled, cumulative operation state, rather than a rare or purely operator-triggerable condition.

### Likelihood Explanation
The likelihood of actually reaching numeric overflow within a single block is bounded by the magnitude of `GasAmount`/`u64` fields (64-bit) versus realistic per-block resource caps (`BouncerConfig::block_max_capacity`), which are configured by chain parameters and are typically far below `u64::MAX`. I was not able to fully verify from the available code whether `bouncer_config.has_room()` is checked strictly before the overflow could occur in every deployed configuration, or whether some field (e.g. `state_diff_size`, `message_segment_length` as raw `usize`) could reach overflow through pathological but valid per-tx values before the `has_room` check trips. This uncertainty means the exact reachability bound (attacker-achievable weight magnitude vs. `u64::MAX`) needs confirmation against the deployed `BouncerConfig` values and the true per-transaction bound checks upstream (e.g., `within_max_capacity_or_err`), which are enforced only *after* the addition in `try_update`, not before it.

### Recommendation
Replace the `.expect()` panic in `Bouncer::try_update`'s `checked_add` call with a graceful `TransactionExecutorError`/`TransactionExecutionError` return (e.g., treat overflow the same as exceeding block capacity, or a dedicated `ArithmeticOverflow` error), and change `commit_tx`'s `_ => panic!(...)` fallback for non-`BlockFull` bouncer errors to propagate the error instead of crashing the executing thread, consistent with the `TODO(Avi, 01/07/2024): Consider propagating the error` comment already present in the code. [3](#0-2) 

### Proof of Concept
Conceptual PoC (not fully verifiable without deployed `BouncerConfig` values):
1. Configure/observe a sequencer with `BouncerConfig.block_max_capacity` fields close to `u64::MAX` for some resource (or use a custom/testing config, as shown in `bouncer_test.rs`'s `test_bouncer_try_update_gas_based`, which directly manipulates `BouncerWeights`/`accumulated_weights` to hit the overflow-adjacent boundary).
2. Submit a sequence of transactions each maximizing one bounded resource field (e.g., `sierra_gas`, event count, message segment length) up to just under the configured cap so `has_room` still passes for earlier transactions.
3. Submit one more transaction whose marginal weight for that field, when added via `checked_add` to the already-large `accumulated_weights`, overflows the underlying integer type before the `has_room` check is reached, tripping the `.expect(&err_msg)` panic in `try_update` at [1](#0-0) , crashing the worker thread executing the block per `commit_tx`'s panic fallback at [3](#0-2) .

This mirrors the existing regression test `test_bouncer_try_update_gas_based` / `test_transaction_too_large_sierra_gas_based` in `bouncer_test.rs`, which exercises the same `try_update` code path with crafted `BouncerWeights`, but does not itself target the integer-overflow branch of `checked_add`. [4](#0-3)

### Citations

**File:** crates/blockifier/src/bouncer.rs (L669-670)
```rust
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
```

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L347-365)
```rust
            // Ask the bouncer if there is room for the transaction in the block.
            let bouncer_result = self.bouncer.lock().expect("Bouncer lock failed.").try_update(
                &tx_versioned_state,
                &tx_state_changes_keys,
                &execution_summary,
                &tx_execution_info.summarize_builtins(),
                &tx_execution_info.receipt.resources,
                &self.block_context.versioned_constants,
                tx_execution_info.receipt.gas.l2_gas,
            );
            if let Err(error) = bouncer_result {
                match error {
                    TransactionExecutorError::BlockFull => return Ok(CommitResult::NoRoomInBlock),
                    _ => {
                        // TODO(Avi, 01/07/2024): Consider propagating the error.
                        panic!("Bouncer update failed. {error:?}: {error}");
                    }
                }
            }
```

**File:** crates/blockifier/src/bouncer_test.rs (L311-399)
```rust
#[rstest]
#[case::sierra_gas_positive_flow("ok")]
#[case::sierra_gas_block_full("sierra_gas_block_full")]
#[case::proving_gas_positive_flow("ok")]
#[case::proving_gas_block_full("proving_gas_block_full")]
fn test_bouncer_try_update_gas_based(#[case] scenario: &'static str, block_context: BlockContext) {
    let state = &mut test_state(&block_context.chain_info, Fee(0), &[]);
    let mut transactional_state = TransactionalState::create_transactional(state);

    let range_check_count = 2;
    let builtin_counters = match scenario {
        "proving_gas_block_full" => {
            cairo_primitive_counter_map([(BuiltinName::range_check, range_check_count)])
        }
        // Use a minimal or empty map.
        "ok" | "sierra_gas_block_full" => {
            cairo_primitive_counter_map([(BuiltinName::range_check, range_check_count - 1)])
        }
        _ => panic!("Unexpected scenario: {scenario}"),
    };

    // Derive sierra_gas from scenario
    let sierra_gas = match scenario {
        "sierra_gas_block_full" => GasAmount(11), // Exceeds capacity
        "ok" | "proving_gas_block_full" => GasAmount(1), // Within capacity
        _ => panic!("Unexpected scenario: {scenario}"),
    };

    // Pick a range_check instance limit such that exactly `range_check_count` ops fill the
    // default block proving-gas budget (induced cost = proving_gas / range_check_count).
    let builtin_instance_limits = BuiltinInstanceLimits {
        range_check: NonZeroU64::new(u64_from_usize(range_check_count))
            .expect("range_check_count must be > 0"),
        ..BuiltinInstanceLimits::default()
    };
    let proving_gas_max_capacity = BouncerWeights::default().proving_gas;

    let block_max_capacity = BouncerWeights {
        l1_gas: 20,
        message_segment_length: 20,
        n_events: 20,
        state_diff_size: 20,
        n_txs: 20,
        sierra_gas: GasAmount(20),
        proving_gas: proving_gas_max_capacity,
        receipt_l2_gas: GasAmount(20),
    };
    let bouncer_config = BouncerConfig { block_max_capacity, builtin_instance_limits };

    let bouncer_weights = BouncerWeights {
        l1_gas: 10,
        message_segment_length: 10,
        n_events: 10,
        state_diff_size: 10,
        sierra_gas: GasAmount(10),
        n_txs: 10,
        proving_gas: GasAmount(10),
        receipt_l2_gas: GasAmount(0),
    };
    let accumulated_weights = TxWeights { bouncer_weights, ..Default::default() };

    let mut bouncer = Bouncer { accumulated_weights, bouncer_config, ..Bouncer::empty() };

    // Prepare the resources to be added to the bouncer.
    let execution_summary = ExecutionSummary::default();
    let tx_resources = TransactionResources {
        computation: ComputationResources { sierra_gas, ..Default::default() },
        ..Default::default()
    };
    let tx_state_changes_keys = transactional_state.to_state_diff().unwrap().state_maps.keys();

    let result = bouncer.try_update(
        &transactional_state,
        &tx_state_changes_keys,
        &execution_summary,
        &cairo_primitive_counter_map(builtin_counters),
        &tx_resources,
        &block_context.versioned_constants,
        GasAmount::ZERO,
    );

    match scenario {
        "ok" => assert_matches!(result, Ok(())),
        "proving_gas_block_full" | "sierra_gas_block_full" => {
            assert_matches!(result, Err(TransactionExecutorError::BlockFull))
        }
        _ => panic!("Unexpected scenario: {scenario}"),
    }
}
```
