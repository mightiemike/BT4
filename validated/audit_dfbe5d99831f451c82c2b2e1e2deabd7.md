### Title
Single request-processing task per component with no panic isolation causes silent component-wide DoS - ([File: crates/apollo_infra/src/component_server/local_component_server.rs])

### Summary
Each Apollo component (Gateway, Mempool, Batcher, etc.) runs its request-processing loop inside one `tokio::spawn`ed task in `LocalComponentServer::process_requests`. That single task pulls requests off a channel in a `loop` and calls `component.handle_request(...)` directly, with no `panic::catch_unwind` guard around the call, analogous to how `TbdController`'s unguarded loop in the EVerest CVE terminates silently and stops serving SDP/ISO15118-20 requests.

### Finding Description
`process_requests` spawns exactly one background task per component that runs an unbounded `loop`, dequeuing a request via `get_next_request_for_processing` and then calling `process_request(&mut component, request, ...)` (which internally calls `component.handle_request(request)`), before looping back for the next request. [1](#0-0) 

There is no `catch_unwind`, no per-request task spawn, and no supervision/restart logic around this loop. If `handle_request` panics while processing a single request — e.g. a `Gateway::add_tx` call for an attacker-submitted transaction that triggers a panic deep in stateful validation / blockifier execution (`crates/apollo_gateway/src/gateway.rs`, `crates/apollo_gateway/src/stateful_transaction_validator.rs`) — the panic unwinds the entire spawned task. Tokio silently drops the task; the loop never resumes, so the channel receiver (`normal_rx`/`high_rx`) is dropped and no further requests to that component are ever processed again, for the lifetime of the process. This is a genuine "one bad transaction terminates the loop silently" bug class matching the CVE description.

Separately, `await_requests` (the task that forwards `rx` into the priority queues) also has no protection: it uses `.expect(...)` on the channel sends and simply logs an error and exits its loop if the incoming channel closes, rather than restarting. [2](#0-1) 

By contrast, the blockifier's concurrent execution path *does* guard against exactly this scenario with an explicit `panic::catch_unwind`/`AbortIfPanic` pattern, showing the project is aware of, and mitigates, this bug class elsewhere but not in the per-component request-processing loop. [3](#0-2) 

This is reachable directly by an unprivileged transaction sender: any transaction reaching `Gateway::add_tx` (via HTTP `POST /gateway/add_rpc_transaction` or `add_transaction`) is dispatched through this exact request-processing loop. [4](#0-3) [5](#0-4) 
The same pattern applies to the Mempool's component request handler (`AddTransaction`, `ValidateTransaction`, `GetTransactions`, `CommitBlock`), which is also served through `LocalComponentServer`/`ConcurrentLocalComponentServer`. [6](#0-5) 

### Impact Explanation
If a crafted transaction (or a contract call it triggers) causes a panic anywhere in the synchronous or async call chain invoked by `handle_request` for Gateway or Mempool — e.g., an `unwrap()`/`expect()` on a codepath believed unreachable but reachable under adversarial calldata, or a stack overflow-adjacent panic in blockifier execution not routed through the guarded `WorkerPool` (e.g. sequential/gateway validation paths that use `execute_txs_sequentially`, which itself uses `.join().expect("Failed to join thread.")` to re-panic on the caller after a native-execution panic) — the entire component's request-processing task dies. From that point, the Gateway (or Mempool) silently stops accepting/processing any further transactions from any sender, i.e., the sequencer becomes unable to admit or confirm new transactions, matching the "network unable to confirm new transactions" bar in the validation rules. This is not merely a resource-exhaustion DoS; it is a permanent halt of a control-plane component that only a full process/service restart can fix.

### Likelihood Explanation
Likelihood depends on the existence of at least one reachable panic in the transaction admission/validation/execution call graph invoked from `handle_request` that is not otherwise guarded by `catch_unwind` (unlike the concurrent `WorkerPool` path). Given the size of the blockifier/gateway validation code and the historical precedent of unwrap/expect/panic usage found throughout (`transaction_pool.rs`, `apollo_reverts`, `commitment_manager_impl.rs`, etc.), the presence of such a panic path is plausible but not concretely proven here — it requires either a fuzzing/code-audit exercise across the stateful validator and blockifier `Result` mapping code, or a specific known trigger (e.g., an edge case in `gen_tx_execution_error_trace`/error-variant mapping in `crates/apollo_rpc_execution/src/execution_test.rs` shows there is already a maintained "TODO: remove once blockifier arranges the errors" mapping layer, suggesting fragility here).

### Recommendation
Wrap the per-request `handle_request` invocation inside `process_request`/`process_requests`'s spawned loop with `std::panic::catch_unwind` (using `AssertUnwindSafe`), convert any caught panic into an internal-error `Response` returned to the caller, and keep the loop alive for subsequent requests — mirroring the pattern already used in `crates/blockifier/src/concurrency/worker_pool.rs`. Additionally, audit `execute_txs_sequentially`'s `.join().expect(...)` re-panic behavior and the stateful transaction validator / gateway `add_tx` path to ensure a single malicious/malformed transaction cannot escape `Result`-based error handling and produce an unguarded panic that kills the owning task.

### Proof of Concept
Conceptual PoC (not executed, since this requires locating/confirming a concrete panic trigger in the validation/execution call graph):
1. Submit an RPC transaction via `POST /gateway/add_rpc_transaction` crafted to hit an `unwrap()`/`expect()`/`panic!()` reachable from `Gateway::add_tx` → stateful/stateless validation → blockifier execution, without being caught as a `Result::Err`.
2. Observe that the spawned task in `LocalComponentServer::process_requests` panics and is dropped by Tokio; the Gateway's channel receiver is dropped.
3. Send a second, entirely valid transaction to the Gateway and observe it is never processed (no response, request queued indefinitely) — confirming the component is permanently unresponsive until restarted.

### Citations

**File:** crates/apollo_infra/src/component_server/local_component_server.rs (L176-209)
```rust
    async fn await_requests(&mut self) {
        info!(
            "Starting to await requests in the component {} local server",
            short_type_name::<Component>()
        );
        while let Some(request_wrapper) = self.rx.recv().await {
            trace!(
                "Component {} received request {:?} with priority {:?}",
                short_type_name::<Component>(),
                request_wrapper.request,
                request_wrapper.request.priority()
            );
            match request_wrapper.request.priority() {
                RequestPriority::High => {
                    self.high_priority_request_tx
                        .send(request_wrapper)
                        .await
                        .expect("Failed to send high priority request");
                }
                RequestPriority::Normal => {
                    self.normal_priority_request_tx
                        .send(request_wrapper)
                        .await
                        .expect("Failed to send low priority request");
                }
            }
            self.metrics.increment_received();
        }

        error!(
            "Stopped awaiting requests in the component {} local server",
            short_type_name::<Component>()
        );
    }
```

**File:** crates/apollo_infra/src/component_server/local_component_server.rs (L223-243)
```rust
        tokio::spawn(async move {
            loop {
                let (request, tx, request_id) = get_next_request_for_processing(
                    &mut high_rx,
                    &mut normal_rx,
                    &component_name,
                    metrics,
                )
                .await;

                process_request(
                    &mut component,
                    request,
                    request_id,
                    tx,
                    metrics,
                    processing_time_warning_threshold_ms,
                )
                .await;
            }
        });
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L138-166)
```rust
    /// Runs a single worker executor.
    fn _run_executor(&self, worker_executor: &WorkerExecutor<S>) {
        if self.a_thread_panicked.load(Ordering::Acquire) {
            panic!("Another thread panicked. Aborting.");
        }

        // Making sure that the program will abort if a panic occurred while halting
        // the scheduler.
        let abort_guard = AbortIfPanic;
        // If a panic is not handled or the handling logic itself panics, then we
        // abort the program.
        let res = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            worker_executor.run();
        }));
        if let Err(err) = res {
            // First, set the panic flag. This must be done before halting the scheduler.
            self.a_thread_panicked.store(true, Ordering::Release);

            // If the program panics here, the abort guard will exit the program.
            // In this case, no panic message will be logged. Add the cargo flag
            // --nocapture to log the panic message.

            worker_executor.scheduler.halt();
            abort_guard.release();
            panic::resume_unwind(err);
        }

        abort_guard.release();
    }
```

**File:** crates/apollo_gateway/src/communication.rs (L19-36)
```rust
#[async_trait]
impl ComponentRequestHandler<GatewayRequest, GatewayResponse> for Gateway {
    async fn handle_request(&mut self, request: GatewayRequest) -> GatewayResponse {
        match request {
            GatewayRequest::AddTransaction(gateway_input) => {
                let p2p_message_metadata = gateway_input.message_metadata.clone();
                GatewayResponse::AddTransaction(
                    self.add_tx(gateway_input.rpc_tx, gateway_input.message_metadata)
                        .await
                        .map_err(|source| GatewayError::DeprecatedGatewayError {
                            source,
                            p2p_message_metadata,
                        }),
                )
            }
        }
    }
}
```

**File:** crates/apollo_http_server/src/http_server.rs (L126-137)
```rust
    pub fn app(&self) -> Router {
        Router::new()
            // Json Rpc endpoint
            .route(
                "/gateway/add_rpc_transaction",
                self.post_method_router(add_rpc_tx),
            )
            // Rest api endpoint
            .route(
                "/gateway/add_transaction",
                self.post_method_router(add_tx),
            )
```

**File:** crates/apollo_mempool/src/communication.rs (L230-264)
```rust
#[async_trait]
impl ComponentRequestHandler<MempoolRequest, MempoolResponse> for MempoolCommunicationWrapper {
    async fn handle_request(&mut self, request: MempoolRequest) -> MempoolResponse {
        // Update the dynamic config before handling the request.
        self.update_dynamic_config().await;
        match request {
            MempoolRequest::ValidateTransaction(args) => {
                MempoolResponse::ValidateTransaction(self.validate_tx(args))
            }
            MempoolRequest::AddTransaction(args) => {
                MempoolResponse::AddTransaction(self.add_tx(args).await)
            }
            MempoolRequest::CommitBlock(args) => {
                MempoolResponse::CommitBlock(self.commit_block(args))
            }
            MempoolRequest::GetTransactions(n_txs) => {
                MempoolResponse::GetTransactions(self.get_txs(n_txs))
            }
            MempoolRequest::AccountTxInPoolOrRecentBlock(account_address) => {
                MempoolResponse::AccountTxInPoolOrRecentBlock(
                    self.account_tx_in_pool_or_recent_block(account_address),
                )
            }
            MempoolRequest::UpdateGasPrice(gas_price) => {
                MempoolResponse::UpdateGasPrice(self.update_gas_price(gas_price))
            }
            MempoolRequest::GetMempoolSnapshot() => {
                MempoolResponse::GetMempoolSnapshot(self.mempool_snapshot())
            }
            MempoolRequest::ResolveBatchTimestamp => {
                MempoolResponse::ResolveBatchTimestamp(self.resolve_batch_timestamp())
            }
        }
    }
}
```
