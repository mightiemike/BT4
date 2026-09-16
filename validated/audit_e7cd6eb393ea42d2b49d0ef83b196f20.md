Confirmed: `RemoteComponentServer::remote_component_server_handler` in `crates/apollo_infra/src/component_server/remote_component_server.rs` performs no caller/peer authentication whatsoever — it deserializes any request body and directly dispatches it to `local_client.send(request)`. [1](#0-0)  This is the exact analog of the `ChainlinkLightClient` bug: privileged, state-mutating handlers exposed over the network with no verification that the caller is the intended, sole authorized peer.

### Title
Missing Access Control on `ClassManager`'s `AddClassAndExecutableUnsafe` RPC Allows Unauthorized Injection of Wrong Class/CASM Mappings - (File: `crates/apollo_infra/src/component_server/remote_component_server.rs`, `crates/apollo_class_manager/src/class_manager.rs`)

### Summary
The `apollo_class_manager` component can be deployed as a network-reachable `RemoteComponentServer` (e.g., `"components.class_manager.execution_mode": "Remote"` in hybrid/distributed topologies). [2](#0-1)  Its `AddClassAndExecutableUnsafe` request is explicitly documented as trusted-caller-only ("This method should only be used through state sync... bypasses compilation - thus unsafe") [3](#0-2) , yet the underlying `RemoteComponentServer` transport layer performs no authentication or peer identity check before forwarding any deserialized request to the component's handler [4](#0-3) . This mirrors the `ChainlinkLightClient` finding: a function meant to be callable only by one authorized caller (there: `Gateway.sol`; here: state sync) is exposed without any `onlyGateway`-equivalent guard.

### Finding Description
`ClassManagerClient::add_class_and_executable_unsafe` writes a caller-supplied `(class_id, class, executable_class_hash_v2, executable_class)` tuple directly into class storage via `ClassManager::add_class_and_executable_unsafe`, which performs **no verification** that `class_id` matches the hash of `class`, nor that `executable_class_hash_v2`/`executable_class` are the correct compiled output of `class` — it simply calls `self.classes.set_class(...)` unconditionally: [5](#0-4) 

This bypass is deliberate for the legitimate state-sync caller, whose upstream flow trusts the feeder gateway/L1 data source and does its own hash consistency checks in `apollo_central_sync` (e.g., panicking on class-hash mismatch for `add_class`) [6](#0-5) . However, nothing in the RPC transport enforces that only the state-sync component can invoke it. The `RemoteComponentServer` handler accepts any TCP client, deserializes the `ClassManagerRequest` enum (which includes `AddClassAndExecutableUnsafe`), and dispatches it to `ClassManager::handle_request` without any peer/service identity check: [7](#0-6) [8](#0-7) 

Any entity capable of reaching the class-manager's bound socket can therefore submit an `AddClassAndExecutableUnsafe` request with an arbitrary `class_id` mapped to attacker-chosen Sierra/CASM content and an arbitrary `executable_class_hash_v2`, silently overwriting/poisoning the class storage that the Gateway, Batcher and Blockifier all read from when executing `Declare`/`Invoke` transactions against that class hash.

### Impact Explanation
If exploited, this allows storing an incorrect CASM (or Sierra) for a given class hash, i.e., data that does not correspond to what would have been produced by the honest Sierra→CASM compilation and class-hashing pipeline. Since the Blockifier subsequently executes whatever executable class is returned by `ClassManager::get_executable`, execution could diverge from what honest nodes computing the same class hash from the correct source would run, causing state/execution divergence between nodes, or a corrupted class definition that permanently makes a class unusable, both of which affect network liveness/consensus over the wrongly-declared class.

### Likelihood Explanation
Likelihood depends entirely on deployment topology: in the "hybrid"/"distributed" topologies the sequencer's own config explicitly enables `class_manager.execution_mode: "Remote"` bound to a TCP port. [2](#0-1)  If that port is reachable by anything other than the trusted state-sync component (e.g., other internal services, or if the deployment's network policy does not perfectly isolate it), exploitation requires only crafting and sending a single well-formed `ClassManagerRequest::AddClassAndExecutableUnsafe` message — no privileged keys or consensus role are needed, matching the "unprivileged transaction sender / contract call" reachability bar once network access to the component is available.

### Recommendation
Add an explicit caller-authentication layer to `RemoteComponentServer`/`RemoteComponentClient` (e.g., mutual TLS, a shared component-to-component secret/token, or restricting `AddClassAndExecutableUnsafe` to only be invocable by a `ClassManagerRequest` variant gated behind a dedicated, separately-authenticated local/internal channel), analogous to the `onlyGateway` modifier recommended for `ChainlinkLightClient`. At minimum, `ClassManager::add_class_and_executable_unsafe` should independently re-validate that `class_id` equals the recomputed hash of `class` before persisting, removing reliance on caller trust alone.

### Proof of Concept
1. Deploy (or target) a sequencer node running in a hybrid/distributed topology with `components.class_manager.execution_mode = "Remote"` and its `remote_server_config` bound and reachable.
2. Craft a raw HTTP/2 request whose body is a `SerdeWrapper`-serialized `ClassManagerRequest::AddClassAndExecutableUnsafe(class_id, class, executable_class_hash_v2, executable_class)` where `class_id` is the hash of a legitimately declared class already known to the network, but `class`/`executable_class` are attacker-controlled bytecode.
3. Send this directly to the class-manager's bound socket/port (bypassing `apollo_central_sync` entirely) — `remote_component_server_handler` will deserialize and forward it to `ClassManager::add_class_and_executable_unsafe`, which stores it with no hash-consistency check [5](#0-4) .
4. Subsequent `GetExecutable(class_id)` calls (used by Gateway/Batcher/Blockifier during transaction execution) now return the poisoned executable class instead of the correct one.

### Citations

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L186-235)
```rust
    #[instrument(skip_all, fields(request_id = %request_id, remote_addr = %client_peer))]
    async fn remote_component_server_handler(
        http_request: HyperRequest<Incoming>,
        request_id: RequestId,
        client_peer: SocketAddr,
        local_client: LocalComponentClient<Request, Response>,
        metrics: &'static RemoteServerMetrics,
        max_request_body_bytes: usize,
    ) -> Result<HyperResponse<Full<Bytes>>, hyper::Error> {
        trace!("Received HTTP request: {http_request:?}");
        let body_bytes =
            match Limited::new(http_request.into_body(), max_request_body_bytes).collect().await {
                Ok(collected) => collected.to_bytes(),
                Err(err) => {
                    warn!("Request body too large: {err}");
                    let server_error = ServerError::RequestBodyTooLarge(err.to_string());
                    return Ok(HyperResponse::builder()
                        .status(StatusCode::PAYLOAD_TOO_LARGE)
                        .header(CONTENT_TYPE, APPLICATION_OCTET_STREAM)
                        .body(Full::new(Bytes::from(
                            SerdeWrapper::new(server_error)
                                .wrapper_serialize()
                                .expect("Server error serialization should succeed"),
                        )))
                        .expect("Response building should succeed"));
                }
            };
        trace!("Extracted {} bytes from HTTP request body", body_bytes.len());

        metrics.increment_total_received();

        let http_response = match SerdeWrapper::<Request>::wrapper_deserialize(&body_bytes)
            .map_err(|err| ClientError::ResponseDeserializationFailure(err.to_string()))
        {
            Ok(request) => {
                trace!(
                    remote_addr = %client_peer,
                    request_id = %request_id,
                    request_type = request.request_label(),
                    "remote component request",
                );
                trace!("Successfully deserialized request: {request:?}");
                metrics.increment_valid_received();

                // Wrap the send operation in a tokio::spawn as it is NOT a cancel-safe operation.
                // Even if the current task is cancelled, the inner task will continue to run.
                // Note: this creates a new request ID for the local client.
                let response = tokio::spawn(async move { local_client.send(request).await })
                    .await
                    .expect("Should be able to extract value from the task");
```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L283-344)
```rust
    async fn start(&mut self) {
        let bind_socket = SocketAddr::new(self.config.bind_ip, self.port);
        debug!(
            "Starting server with socket {:?} with {:?} concurrent connections",
            bind_socket, self.config.max_concurrency
        );
        let connection_semaphore = Arc::new(Semaphore::new(self.config.max_concurrency));

        let per_connection_service =
            |io: TokioIo<tokio::net::TcpStream>,
             max_streams: u32,
             keepalive_interval: Duration,
             keepalive_timeout: Duration,
             connection_semaphore: Arc<Semaphore>,
             local_client: LocalComponentClient<Request, Response>,
             metrics: &'static RemoteServerMetrics,
             max_request_body_bytes: usize,
             client_peer: SocketAddr| {
                async move {
                    trace!(remote_addr = %client_peer, "remote component TCP connection opened");
                    match connection_semaphore.try_acquire_owned() {
                        Ok(permit) => {
                            metrics.increment_number_of_connections();
                            trace!("Acquired semaphore permit for connection");
                            let client_peer_for_handler = client_peer;
                            let handle_request_service =
                                service_fn(move |req: HyperRequest<Incoming>| {
                                    trace!("Received request: {:?}", req);
                                    let request_id = req
                                        .headers()
                                        .get(REQUEST_ID_HEADER)
                                        .and_then(|header| header.to_str().ok())
                                        .and_then(|s| s.parse::<RequestId>().ok())
                                        .expect(
                                            "Request ID should be present in the request headers",
                                        );
                                    Self::remote_component_server_handler(
                                        req,
                                        request_id,
                                        client_peer_for_handler,
                                        local_client.clone(),
                                        metrics,
                                        max_request_body_bytes,
                                    )
                                });

                            // Bundle the service and the acquired permit to limit concurrency at
                            // the connection level.
                            let service = PermitGuardedService {
                                inner: handle_request_service,
                                _permit: Some(permit),
                                remote_server_metrics: metrics,
                            };

                            serve_connection!(
                                io,
                                service,
                                max_streams,
                                keepalive_interval,
                                keepalive_timeout
                            );
                            trace!(remote_addr = %client_peer, "remote component TCP connection closed");
```

**File:** crates/apollo_deployments/resources/services/hybrid/gateway.json (L13-27)
```json
  "components.class_manager.execution_mode": "Remote",
  "components.class_manager.local_server_config.#is_none": true,
  "components.class_manager.max_concurrency": 128,
  "components.class_manager.port": 1,
  "components.class_manager.remote_client_config.#is_none": false,
  "components.class_manager.remote_client_config.attempts_per_log": 1,
  "components.class_manager.remote_client_config.connection_timeout_ms": 500,
  "components.class_manager.remote_client_config.idle_connections": 10,
  "components.class_manager.remote_client_config.keepalive_timeout_ms": 30000,
  "components.class_manager.remote_client_config.initial_retry_delay_ms": 1,
  "components.class_manager.remote_client_config.max_retry_interval_ms": 1000,
  "components.class_manager.remote_client_config.retries": 150,
  "components.class_manager.remote_client_config.set_tcp_nodelay": true,
  "components.class_manager.remote_server_config.#is_none": true,
  "components.class_manager.url": "remote_service",
```

**File:** crates/apollo_class_manager_types/src/lib.rs (L77-85)
```rust
    // This method should only be used through state sync.
    // It acts as a writer to the class storage, and bypasses compilation - thus unsafe.
    async fn add_class_and_executable_unsafe(
        &self,
        class_id: ClassId,
        class: Class,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: ExecutableClass,
    ) -> ClassManagerClientResult<()>;
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L146-155)
```rust
    #[instrument(skip(self, class, executable_class), ret, err)]
    pub fn add_class_and_executable_unsafe(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> ClassManagerResult<()> {
        Ok(self.classes.set_class(class_id, class, executable_class_hash_v2, executable_class)?)
    }
```

**File:** crates/apollo_central_sync/src/lib.rs (L514-522)
```rust
                for (expected_class_hash, class) in &classes {
                    let class_hash =
                        class_manager_client.add_class(class.clone()).await?.class_hash;
                    if class_hash != *expected_class_hash {
                        panic!(
                            "Class hash mismatch. Expected: {expected_class_hash}, got: \
                             {class_hash}."
                        );
                    }
```

**File:** crates/apollo_class_manager/src/communication.rs (L19-46)
```rust
#[async_trait]
impl ComponentRequestHandler<ClassManagerRequest, ClassManagerResponse> for ClassManager {
    async fn handle_request(&mut self, request: ClassManagerRequest) -> ClassManagerResponse {
        let dynamic_config: apollo_class_manager_config::config::ClassManagerDynamicConfig = self
            .0
            .config_manager_client
            .get_class_manager_dynamic_config()
            .await
            .expect("Should be able to get class manager dynamic config");
        self.0.update_dynamic_config(dynamic_config);

        match request {
            ClassManagerRequest::AddClass(class) => {
                ClassManagerResponse::AddClass(self.0.add_class(class).await)
            }
            ClassManagerRequest::AddClassAndExecutableUnsafe(
                class_id,
                class,
                executable_class_hash_v2,
                executable_class,
            ) => ClassManagerResponse::AddClassAndExecutableUnsafe(
                self.0.add_class_and_executable_unsafe(
                    class_id,
                    class,
                    executable_class_hash_v2,
                    executable_class,
                ),
            ),
```
