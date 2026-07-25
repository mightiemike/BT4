The critical evidence is now clear. Let me confirm the exact `view_client_send` helper and the channel type before writing the final determination.

The full code path is now traced. Here is the determination:

---

### Title
Unbounded `ViewClientActor` Mailbox Allows Unprivileged RPC Flooding to Exhaust Node Memory — (`core/async/src/multithread/runtime_handle.rs`, `chain/jsonrpc/src/lib.rs`)

### Summary

An unprivileged attacker can send an unlimited number of concurrent `EXPERIMENTAL_call_function` (or any other `view_client_send`-backed) RPC requests to a nearcore node. Each request enqueues a closure into the `ViewClientActor`'s `crossbeam_channel::unbounded` mailbox. Because the channel is explicitly unbounded and the Axum RPC server applies no rate limiting, connection limit, or backpressure, the queue grows without bound, exhausting node memory and causing a non-network-level denial of service.

### Finding Description

**Entrypoint — no authentication required:**

`EXPERIMENTAL_call_function` is a public POST endpoint. The Axum router applies only CORS and a 10 MB body-size limit:

```rust
app.layer(get_cors(&cors_allowed_origins))
    .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
``` [1](#0-0) 

No per-IP rate limiting, no concurrent-request cap, no authentication.

**RPC dispatch — directly enqueues to ViewClientActor:**

```rust
"EXPERIMENTAL_call_function" => {
    process_sharded_method_call(
        request, source,
        |params| self.call_function_sharded(params),
        |params| self.call_function_local(params),
    ).await
}
``` [2](#0-1) 

`call_function_local` calls `view_client_send` with a `ClientQuery`: [3](#0-2) 

**`view_client_send` sends to an explicitly unbounded crossbeam channel:**

`ViewClientSenderForRpc` wraps `AsyncSender<ClientQuery, Result<QueryResponse, QueryError>>`, which is backed by `MultithreadRuntimeHandle<ViewClientActor>`. The handle's `send_async` implementation pushes a `Box<dyn FnOnce>` closure into the channel: [4](#0-3) 

The channel itself is created as:

```rust
let (sender, receiver) = crossbeam_channel::unbounded::<MultithreadRuntimeMessage<A>>();
``` [5](#0-4) 

There is no bounded variant, no capacity check, and no backpressure signal returned to the caller.

**Each queued message holds:**
- A heap-allocated `Box<dyn FnOnce(&mut ViewClientActor) + Send>` closure (capturing the `ClientQuery` and a `tokio::sync::oneshot::Sender`)
- The `ClientQuery` itself (account ID, method name, args, block reference)

**The handler is CPU-intensive:** `call_function` executes WASM via the runtime adapter, bounded only by `max_gas_burnt_view`. Under load the thread pool saturates and the queue grows faster than it drains. [6](#0-5) 

### Impact Explanation

An attacker opens many concurrent HTTP connections and fires `EXPERIMENTAL_call_function` requests targeting any account (even a non-existent one — the message is still enqueued and processed). The `ViewClientActor` mailbox grows without bound. At sufficient request rate, the node's heap is exhausted, causing OOM termination or severe memory pressure that stalls block processing. This is a non-network-level DoS fixable without a hardfork.

### Likelihood Explanation

The endpoint is public, requires no credentials, no on-chain account, no tokens, and no privileged access. Any attacker with HTTP access to port 3030 can trigger this. The `call_function` handler is among the most expensive view operations (WASM execution), maximizing queue growth rate relative to drain rate.

### Recommendation

1. **Bound the multithread actor channel**: Replace `crossbeam_channel::unbounded()` with `crossbeam_channel::bounded(N)` in `spawn_multithread_actor`, and return `AsyncSendError::Closed` (or a new `QueueFull` variant) when the channel is full. The RPC layer already maps `AsyncSendError` to an `InternalError` response. [7](#0-6) 

2. **Add per-IP or global rate limiting** at the Axum layer for view-function RPC methods.

3. **Add a concurrent-request semaphore** in `JsonRpcHandler` for `view_client_send` calls, so that at most `K` view queries are in-flight at any time, providing backpressure to HTTP clients.

### Proof of Concept

```rust
// Pseudocode: flood EXPERIMENTAL_call_function with concurrent requests
let client = reqwest::Client::new();
let mut handles = vec![];
for _ in 0..10_000 {
    let c = client.clone();
    handles.push(tokio::spawn(async move {
        c.post("http://node:3030")
            .json(&serde_json::json!({
                "jsonrpc": "2.0", "id": "x", "method": "EXPERIMENTAL_call_function",
                "params": {
                    "request_type": "call_function",
                    "finality": "final",
                    "account_id": "any.near",
                    "method_name": "expensive_view",
                    "args_base64": ""
                }
            }))
            .send().await
    }));
}
// Observe ViewClientActor queue depth via /debug/api/instrumented_threads
// and RSS growth via /proc/self/status on the node process.
```

The `InstrumentedQueue` already tracks queue depth per actor; a test can assert that depth grows monotonically and unboundedly under this load. [8](#0-7) [9](#0-8)

### Citations

**File:** chain/jsonrpc/src/lib.rs (L455-479)
```rust
#[derive(Clone, near_async::MultiSend, near_async::MultiSenderFrom)]
pub struct ViewClientSenderForRpc(
    AsyncSender<GetBlock, Result<BlockView, GetBlockError>>,
    AsyncSender<GetBlockProof, Result<GetBlockProofResponse, GetBlockProofError>>,
    AsyncSender<GetChunk, Result<ChunkView, GetChunkError>>,
    AsyncSender<GetExecutionOutcome, Result<GetExecutionOutcomeResponse, GetExecutionOutcomeError>>,
    AsyncSender<GetGasPrice, Result<GasPriceView, GetGasPriceError>>,
    AsyncSender<GetMaintenanceWindows, Result<MaintenanceWindowsView, GetMaintenanceWindowsError>>,
    AsyncSender<
        GetNextLightClientBlock,
        Result<Option<Arc<LightClientBlockView>>, GetNextLightClientBlockError>,
    >,
    AsyncSender<GetProtocolConfig, Result<ProtocolConfigView, GetProtocolConfigError>>,
    AsyncSender<GetReceipt, Result<Option<ReceiptView>, GetReceiptError>>,
    AsyncSender<GetReceiptToTx, Result<GetReceiptToTxResponse, GetReceiptToTxError>>,
    AsyncSender<GetSplitStorageInfo, Result<SplitStorageInfoView, GetSplitStorageInfoError>>,
    AsyncSender<GetChunkExtraExists, Result<bool, GetStateChangesError>>,
    AsyncSender<GetStateChanges, Result<StateChangesView, GetStateChangesError>>,
    AsyncSender<GetStateChangesInBlock, Result<StateChangesKindsView, GetStateChangesError>>,
    AsyncSender<GetValidatorInfo, Result<EpochValidatorInfo, GetValidatorInfoError>>,
    AsyncSender<GetValidatorOrdered, Result<Vec<ValidatorStakeView>, GetValidatorInfoError>>,
    AsyncSender<ClientQuery, Result<QueryResponse, QueryError>>,
    AsyncSender<TxStatus, Result<TxStatusOutcome, TxStatusError>>,
    #[cfg(feature = "test_features")] Sender<near_client::NetworkAdversarialMessage>,
);
```

**File:** chain/jsonrpc/src/lib.rs (L735-743)
```rust
            "EXPERIMENTAL_call_function" => {
                process_sharded_method_call(
                    request,
                    source,
                    |params| self.call_function_sharded(params),
                    |params| self.call_function_local(params),
                )
                .await
            }
```

**File:** chain/jsonrpc/src/lib.rs (L1878-1891)
```rust
    async fn call_function_local(
        &self,
        request_data: RpcCallFunctionRequest,
    ) -> Result<RpcCallFunctionResponse, RpcCallFunctionError> {
        let result = self
            .view_client_send(ClientQuery::new(
                request_data.block_reference,
                QueryRequest::CallFunction {
                    account_id: request_data.account_id,
                    method_name: request_data.method_name,
                    args: request_data.args,
                },
            ))
            .await;
```

**File:** chain/jsonrpc/src/lib.rs (L3119-3121)
```rust
    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```

**File:** core/async/src/multithread/sender.rs (L40-63)
```rust
    fn send_async(&self, message: M) -> BoxFuture<'static, Result<R, AsyncSendError>> {
        let seq = next_message_sequence_num();
        let message_type = pretty_type_name::<M>();
        tracing::trace!(target: "multithread_runtime", seq, message_type, ?message, "sending async message");

        let (sender, receiver) = tokio::sync::oneshot::channel();
        let future = async move { receiver.await.map_err(|_| AsyncSendError::Dropped) };
        let function = move |actor: &mut A| {
            let result = actor.handle(message);
            sender.send(result).ok(); // OK if the sender doesn't care about the result anymore.
        };

        let message = MultithreadRuntimeMessage {
            seq,
            enqueued_time_ns: self.instrumentation.current_time(),
            name: message_type,
            function: Box::new(function),
        };
        if let Err(_) = self.send_message(message) {
            async { Err(AsyncSendError::Dropped) }.boxed()
        } else {
            future.boxed()
        }
    }
```

**File:** core/async/src/multithread/runtime_handle.rs (L48-58)
```rust
impl<A> MultithreadRuntimeHandle<A> {
    pub(super) fn send_message(
        &self,
        message: MultithreadRuntimeMessage<A>,
    ) -> Result<(), crossbeam_channel::SendError<MultithreadRuntimeMessage<A>>> {
        let name = message.name;
        self.sender.send(message).map(|_| {
            // Only increment the queue if the message was successfully sent.
            self.instrumentation.queue().enqueue(name);
        })
    }
```

**File:** core/async/src/multithread/runtime_handle.rs (L82-90)
```rust
    let (sender, receiver) = crossbeam_channel::unbounded::<MultithreadRuntimeMessage<A>>();
    let instrumented_queue = InstrumentedQueue::new(actor_name);
    let shared_instrumentation =
        InstrumentedThreadWriterSharedPart::new(actor_name.to_string(), instrumented_queue.clone());
    let handle = MultithreadRuntimeHandle {
        sender,
        cancellation_signal_holder,
        instrumentation: shared_instrumentation,
    };
```

**File:** runtime/runtime/src/state_viewer/mod.rs (L406-456)
```rust
    pub fn call_function(
        &self,
        mut state_update: TrieUpdate,
        view_state: ViewApplyState,
        contract_id: &AccountId,
        method_name: &str,
        args: &[u8],
        logs: &mut Vec<String>,
        epoch_info_provider: &dyn EpochInfoProvider,
    ) -> Result<Vec<u8>, errors::CallFunctionError> {
        assert_supported_protocol_version(view_state.current_protocol_version);
        let now = Instant::now();
        let root = *state_update.get_root();
        let account = get_account(&state_update, contract_id)?.ok_or_else(|| {
            errors::CallFunctionError::AccountDoesNotExist {
                requested_account_id: contract_id.clone(),
            }
        })?;
        // TODO(#1015): Add ability to pass public key and originator_id
        let originator_id = contract_id;
        let public_key = PublicKey::empty(KeyType::ED25519);
        let empty_hash = CryptoHash::default();
        let mut receipt_manager = ReceiptManager::default();
        let config = self.runtime_config_store.get_config(view_state.current_protocol_version);
        let apply_state = ApplyState {
            apply_reason: ApplyChunkReason::ViewTrackedShard,
            block_height: view_state.block_height,
            // Used for legacy reasons
            prev_block_hash: view_state.prev_block_hash,
            shard_id: view_state.shard_id,
            epoch_id: view_state.epoch_id,
            epoch_height: view_state.epoch_height,
            gas_price: Balance::ZERO,
            block_timestamp: view_state.block_timestamp,
            gas_limit: None,
            random_seed: root,
            current_protocol_version: view_state.current_protocol_version,
            config: Arc::clone(config),
            next_wasm_config: None,
            cache: view_state.cache,
            is_new_chunk: false,
            save_receipt_to_tx: false,
            congestion_info: Default::default(),
            bandwidth_requests: BlockBandwidthRequests::empty(),
            trie_access_tracker_state: Default::default(),
            on_post_state_ready: None,
        };
        let function_call = FunctionCallAction {
            method_name: method_name.to_string(),
            args: args.to_vec(),
            gas: self.max_gas_burnt_view(view_state.current_protocol_version),
```
