### Title
Unbounded Task Spawning in Gateway HTTP Server Enables Memory Exhaustion via Stream/Request Amplification - (File: crates/apollo_http_server/src/http_server.rs)

### Summary
The gateway's HTTP ingestion server (`apollo_http_server`) — the entry point through which any unprivileged, unauthenticated network client submits transactions to the sequencer — has no bound on the number of concurrent requests, connections, or spawned tasks it will accept. Unlike other server components in the same codebase (`RemoteComponentServer`, which enforces a `connection_semaphore` and `max_streams_per_connection`), the public-facing `HttpServer::app()` router only applies body-size limits (`RequestBodyLimitLayer`, `DefaultBodyLimit`) and decompression, with no `tower::limit::ConcurrencyLimitLayer`, connection semaphore, or `max_concurrency` setting of any kind.

### Finding Description
`HttpServer::run` binds a `TcpListener` and calls `serve(listener, app)` with no wrapping to cap concurrent connections: [1](#0-0) 

The router construction (`HttpServer::app`) only layers request body-size limiting and decompression — there is no concurrency-limiting layer: [2](#0-1) 

Critically, every accepted request that reaches the `add_tx`/`add_rpc_tx` handlers results in an *additional* `tokio::spawn` inside `add_tx_inner`, independent of the task hyper/axum already spawns per connection/request: [3](#0-2) 

The corresponding config type, `HttpServerConfig`/`HttpServerStaticConfig`, exposes only `max_request_body_size`, `dynamic_config_poll_interval`, `ip`, and `port` — there is no `max_concurrency`, `max_connections`, or semaphore-based admission control field: [4](#0-3) 

This is structurally the same bug class as CVE-2025-47950 (CoreDNS DoQ): a public network-facing server creates a new task/goroutine per incoming unit of work (QUIC stream ↔ here, HTTP request/connection) with no limit on concurrency, allowing a single remote, unauthenticated party to drive unbounded goroutine/task and memory growth. In this codebase, the fix pattern already exists and is used elsewhere — `RemoteComponentServer::run` explicitly acquires an `OwnedSemaphorePermit` per connection and enforces `max_streams_per_connection` before spawning `per_connection_service`: [5](#0-4) [6](#0-5) 

The gateway's public HTTP entry point (`apollo_http_server`), however, applies none of these protections, despite being the component directly reachable by an arbitrary, unauthenticated internet client submitting transactions.

### Impact Explanation
An unauthenticated remote attacker can open a very large number of concurrent HTTP connections/requests to `/gateway/add_transaction` or `/gateway/add_rpc_transaction`. Each accepted request:
- Consumes a hyper/tokio connection task,
- Additionally spawns a dedicated `tokio::spawn`'d task in `add_tx_inner` that holds the full `GatewayInput`/`RpcTransaction` payload (up to `max_request_body_size`, default 5 MB) and a tracing span for the lifetime of the inner gateway call,
- Is not gated by any semaphore or concurrency cap.

Because request bodies can each approach the 5 MB body limit and there is no cap on how many such requests/tasks can be in flight simultaneously, an attacker can drive the process's memory usage to the point of OOM kill, taking down the gateway/sequencer node and preventing legitimate transaction submission — a network-unable-to-confirm-new-transactions condition. This matches the "High availability loss" impact class of the reference CVE.

### Likelihood Explanation
High. The vulnerable path (`/gateway/add_transaction`, `/gateway/add_rpc_transaction`) is the primary, intentionally public transaction-ingestion endpoint — reachable by any single unprivileged sender with no authentication, no special permissions, and minimal cost (just opening TCP connections/HTTP requests). No malicious operator, prover, or peer status is required; a single external actor with an HTTP client can trigger this.

### Recommendation
Apply the same admission-control pattern already used in `apollo_infra::component_server::remote_component_server` to `apollo_http_server::http_server::HttpServer`:
- Introduce a configurable `max_concurrency` / connection-count limit (e.g., a `tower::limit::ConcurrencyLimitLayer` or an `Arc<Semaphore>` acquired per accepted connection/request) in `HttpServer::app()` / `HttpServer::run()`.
- Bound the number of in-flight `add_tx_inner` spawned tasks (e.g., via a bounded semaphore acquired before `tokio::spawn`, mirroring `PermitGuardedService`), rejecting excess requests with `503 Service Unavailable` rather than queuing/spawning unboundedly.
- Add corresponding fields to `HttpServerStaticConfig` (e.g., `max_concurrent_requests`) with sane defaults, and document them in `config_schema.json`.

### Proof of Concept
1. Start a sequencer node with `apollo_http_server` enabled (default deployment topology, `components.http_server.execution_mode = Enabled`).
2. From an unauthenticated client, open N concurrent TCP connections (N in the tens of thousands) to the gateway's bound address and issue POST requests to `/gateway/add_transaction` or `/gateway/add_rpc_transaction`, each with a payload sized close to `max_request_body_size` (5 MB default).
3. Observe that `HttpServer::app()` (crates/apollo_http_server/src/http_server.rs:126-153) applies no concurrency cap, and each request causes an additional `tokio::spawn` in `add_tx_inner` (lines 296-326) that retains the parsed transaction and tracing span until the inner `gateway_client.add_tx` call resolves.
4. As concurrent connections/requests scale, resident memory grows unbounded relative to any configured limit, eventually triggering an OOM kill of the sequencer's gateway process — denying service to all legitimate transaction senders.

### Citations

**File:** crates/apollo_http_server/src/http_server.rs (L120-123)
```rust
        // Create a server that runs forever.
        let listener = TcpListener::bind(&addr).await?;
        Ok(serve(listener, app).await?)
    }
```

**File:** crates/apollo_http_server/src/http_server.rs (L126-153)
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
            // TODO(shahak): Remove this once we fix the centralized simulator to not use is_alive
            // and is_ready.
            .route(
                "/gateway/is_alive",
                get(|| futures::future::ready("Gateway is alive".to_owned()))
            )
            .route("/gateway/is_ready", get(is_ready))
            .layer(Extension(self.app_state.clone()))
            // Hard streaming limit on decompressed bytes — wraps the body in
            // http_body_util::Limited which errors during poll_frame() once the
            // limit is exceeded, preventing zip bombs from expanding in memory.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
            .layer(RequestDecompressionLayer::new())
            // Cap compressed wire bytes to bound network I/O.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
    }
```

**File:** crates/apollo_http_server/src/http_server.rs (L296-326)
```rust
async fn add_tx_inner(
    app_state: AppState,
    headers: HeaderMap,
    tx: RpcTransaction,
) -> HttpServerResult<Json<GatewayOutput>> {
    debug!("Received transaction: {tx:?}");
    let gateway_input: GatewayInput = GatewayInput { rpc_tx: tx, message_metadata: None };
    // Wrap the gateway client interaction with a tokio::spawn as it is NOT cancel-safe.
    // Even if the current task is cancelled, e.g., when a request is dropped while still being
    // processed, the inner task will continue to run.
    let region = headers
        .get(CLIENT_REGION_HEADER)
        .and_then(|region| region.to_str().ok())
        .unwrap_or("N/A")
        .to_string();
    let add_tx_result = tokio::spawn(
        async move {
            let add_tx_result = app_state.gateway_client.add_tx(gateway_input).await.map_err(|e| {
                debug!("Error while adding transaction: {}", e);
                HttpServerError::from(Box::new(e))
            });
            record_added_transactions(&add_tx_result, &region);
            add_tx_result
        }
        .instrument(tracing::Span::current()),
    )
    .await
    .expect("Should be able to get add_tx result");

    Ok(Json(add_tx_result?))
}
```

**File:** crates/apollo_http_server_config/src/config.rs (L51-58)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct HttpServerStaticConfig {
    pub ip: IpAddr,
    pub port: u16,
    pub max_request_body_size: usize,
    #[serde(deserialize_with = "deserialize_milliseconds_to_duration")]
    pub dynamic_config_poll_interval: Duration,
}
```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L300-336)
```rust
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

```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L401-439)
```rust
        let max_streams = self.config.max_streams_per_connection;
        let keepalive_interval = Duration::from_millis(self.config.keepalive_interval_ms);
        let keepalive_timeout = Duration::from_millis(self.config.keepalive_timeout_ms);

        loop {
            let (stream, peer_addr) = match listener.accept().await {
                Ok(conn) => conn,
                Err(e) => {
                    error!("Failed to accept connection: {e}");
                    tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;
                    continue;
                }
            };

            if let Err(e) = stream.set_nodelay(self.config.set_tcp_nodelay) {
                warn!("Failed to set TCP_NODELAY: {e}");
            }

            let tcp_keepalive = TcpKeepalive::new()
                .with_time(keepalive_timeout.mul_f64(TCP_KEEPALIVE_FACTOR))
                .with_interval(keepalive_interval)
                .with_retries(TCP_KEEPALIVE_RETRIES);
            if let Err(e) = SockRef::from(&stream).set_tcp_keepalive(&tcp_keepalive) {
                error!("Failed to set TCP keepalive: {e}");
            }

            let io = TokioIo::new(stream);

            tokio::spawn(per_connection_service(
                io,
                max_streams,
                keepalive_interval,
                keepalive_timeout,
                connection_semaphore.clone(),
                self.local_client.clone(),
                self.metrics,
                self.config.max_request_body_bytes,
                peer_addr,
            ));
```
