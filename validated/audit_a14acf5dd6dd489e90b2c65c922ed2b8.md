### Title
MOOSDB-style unbounded connection/thread exhaustion in the public gateway HTTP server - (File: crates/apollo_http_server/src/http_server.rs)

### Summary
The `apollo_http_server` component — the entry point that unprivileged transaction senders use to submit transactions (`/gateway/add_rpc_transaction`, `/gateway/add_transaction`) — runs `axum::serve(listener, app)` with no connection cap, no per-connection concurrency limit, and no header/idle-read timeout, unlike the internal `RemoteComponentServer`, which explicitly bounds concurrent connections via a `Semaphore`, sets HTTP/2 keepalive interval/timeout, and TCP keepalive probes.

### Finding Description
`HttpServer::run` binds a `TcpListener` and calls `serve(listener, app)` directly [1](#0-0) , with no wrapping `axum::serve(...).with_graceful_shutdown` connection accounting, no `tower::limit::ConcurrencyLimitLayer`, and no request/header timeout layer anywhere in the router construction [2](#0-1) . The only protections applied are body-size limits (`RequestBodyLimitLayer`, `DefaultBodyLimit`) which bound the size of a single already-accepted request body, not the number of concurrent connections or the time a connection/stream can remain open while trickling header/body bytes [3](#0-2) .

`HttpServerStaticConfig` likewise exposes only `ip`, `port`, `max_request_body_size`, and `dynamic_config_poll_interval` — there is no `max_connections`, `max_concurrency`, `keepalive_timeout`, or header-read-timeout field [4](#0-3) .

By contrast, the sequencer's internal `RemoteComponentServer` (used for inter-component RPC, not directly reachable from untrusted senders) explicitly guards against exactly this class of bug: it wraps every accepted connection in a `Semaphore`-acquired permit, rejecting connections beyond `max_concurrency` with `503`, and sets HTTP/2 `keep_alive_interval`/`keep_alive_timeout` plus TCP-level keepalive with bounded retries [5](#0-4) [6](#0-5) . No equivalent connection-cap or slow-header-timeout mechanism exists in the public-facing `apollo_http_server`, which is precisely the bug class described in the MOOSDB HTTP server report: unbounded connections/threads with no limits, allowing an attacker to open many connections and stall on header/body delivery to exhaust threads and memory.

### Impact Explanation
An unprivileged remote client can open an unbounded number of TCP connections to the gateway's HTTP server (the node's only externally reachable transaction-submission endpoint) and either hold them open (slow headers/slow body, "slowloris"-style) or open connections faster than they are serviced. Since `serve()` here spawns a task per connection with no cap and no idle/header timeout, this exhausts server memory/file descriptors/tokio task slots, causing the gateway to become unresponsive to legitimate transaction submissions — a network unable to confirm new transactions, matching the required impact bar.

### Likelihood Explanation
High reachability: the endpoint is intentionally public and requires no authentication, no valid transaction content, and no special privilege — merely opening TCP connections and sending partial/slow data is sufficient. This matches the "single submitted transaction / unprivileged sender" reachability required, since the attacker doesn't even need to complete a valid request.

### Recommendation
Apply the same defenses already used in `RemoteComponentServer` to the public `apollo_http_server`: bound total/concurrent connections (e.g., a `Semaphore`-based accept-and-reject scheme or `tower::limit::ConcurrencyLimitLayer`/`GlobalConcurrencyLimitLayer`), add header-read and idle-connection timeouts (e.g., using `hyper_util` server builder with `http1_header_read_timeout`/similar, or a `tower_http::timeout::TimeoutLayer`), and add corresponding `HttpServerStaticConfig` fields (`max_connections`, `header_read_timeout_ms`, `idle_timeout_ms`) with sane defaults.

### Proof of Concept
1. Start the sequencer's `apollo_http_server` component with default config.
2. From an external client, open several thousand raw TCP connections to the gateway's bound port and, for each, send request headers at an extremely slow rate (a few bytes every few seconds) or simply hold the connection open without completing the HTTP request.
3. Because `serve(listener, app)` in `crates/apollo_http_server/src/http_server.rs` spawns a task per accepted connection with no concurrency cap and no header-read timeout, each connection consumes server resources indefinitely.
4. Legitimate transaction submissions to `/gateway/add_rpc_transaction` / `/gateway/add_transaction` begin to fail or time out as the server's threads/memory are exhausted, demonstrating denial of service of the gateway's transaction-intake path.

### Citations

**File:** crates/apollo_http_server/src/http_server.rs (L120-123)
```rust
        // Create a server that runs forever.
        let listener = TcpListener::bind(&addr).await?;
        Ok(serve(listener, app).await?)
    }
```

**File:** crates/apollo_http_server/src/http_server.rs (L126-162)
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

    fn post_method_router<H, T, S>(&self, handler: H) -> MethodRouter<S>
    where
        H: Handler<T, S> + Send + Sync + 'static,
        T: Send + 'static,
        S: Clone + Send + Sync + 'static,
    {
        post(handler).layer(DefaultBodyLimit::max(self.config.static_config.max_request_body_size))
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

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L303-343)
```rust
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
```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L405-440)
```rust
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
        }
```
