[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** mempool/src/shared_mempool/coordinator.rs (L90-93)
```rust
    // Use a BoundedExecutor to restrict only `workers_available` concurrent
    // worker tasks that can process incoming transactions.
    let workers_available = smp.config.shared_mempool_max_concurrent_inbound_syncs;
    let bounded_executor = BoundedExecutor::new(workers_available, executor.clone());
```

**File:** mempool/src/shared_mempool/coordinator.rs (L106-111)
```rust
    loop {
        let _timer = counters::MAIN_LOOP.start_timer();
        ::futures::select! {
            msg = client_events.select_next_some() => {
                handle_client_request(&mut smp, &bounded_executor, msg).await;
            },
```

**File:** mempool/src/shared_mempool/tasks.rs (L199-209)
```rust
    timer.stop_and_record();
    let _timer = counters::process_get_txn_latency_timer_client();
    let txn = smp.mempool.lock().get_by_hash(hash);

    if callback.send(txn).is_err() {
        warn!(LogSchema::event_log(
            LogEntry::GetTransaction,
            LogEvent::CallbackFail
        ));
        counters::CLIENT_CALLBACK_FAIL.inc();
    }
```

**File:** mempool/src/shared_mempool/tasks.rs (L590-632)
```rust
    let (transactions, validation_results): (Vec<_>, Vec<_>) = results.into_iter().unzip();
    vm_validation_timer.stop_and_record();
    {
        let mut mempool = smp.mempool.lock();
        for (idx, (transaction, account_sequence_number, ready_time_at_sender, priority)) in
            transactions.into_iter().enumerate()
        {
            if let Ok(validation_result) = &validation_results[idx] {
                match validation_result.status() {
                    None => {
                        let ranking_score = validation_result.score();
                        let mempool_status = mempool.add_txn(
                            transaction.clone(),
                            ranking_score,
                            account_sequence_number,
                            timeline_state,
                            client_submitted,
                            ready_time_at_sender,
                            priority.clone(),
                        );
                        statuses.push((transaction, (mempool_status, None)));
                    },
                    Some(validation_status) => {
                        statuses.push((
                            transaction.clone(),
                            (
                                MempoolStatus::new(MempoolStatusCode::VmError),
                                Some(validation_status),
                            ),
                        ));
                    },
                }
            } else {
                statuses.push((
                    transaction.clone(),
                    (
                        MempoolStatus::new(MempoolStatusCode::VmError),
                        Some(DiscardedVMStatus::UNKNOWN_STATUS),
                    ),
                ));
            }
        }
    }
```

**File:** mempool/src/shared_mempool/tasks.rs (L736-762)
```rust
            {
                let lock_timer = counters::mempool_service_start_latency_timer(
                    counters::GET_BLOCK_LOCK_LABEL,
                    counters::REQUEST_SUCCESS_LABEL,
                );
                let mut mempool = smp.mempool.lock();
                lock_timer.observe_duration();

                {
                    let _gc_timer = counters::mempool_service_start_latency_timer(
                        counters::GET_BLOCK_GC_LABEL,
                        counters::REQUEST_SUCCESS_LABEL,
                    );
                    // gc before pulling block as extra protection against txns that may expire in consensus
                    // Note: this gc operation relies on the fact that consensus uses the system time to determine block timestamp
                    let curr_time = aptos_infallible::duration_since_epoch();
                    mempool.gc_by_expiration_time(curr_time);
                }

                let max_txns = cmp::max(max_txns, 1);
                let _get_batch_timer = counters::mempool_service_start_latency_timer(
                    counters::GET_BLOCK_GET_BATCH_LABEL,
                    counters::REQUEST_SUCCESS_LABEL,
                );
                txns =
                    mempool.get_batch(max_txns, max_bytes, return_non_full, exclude_transactions);
            }
```
