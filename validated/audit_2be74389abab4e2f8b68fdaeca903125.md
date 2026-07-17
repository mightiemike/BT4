### Title
Trace Query Server Hardcoded to `0.0.0.0` Exposes Raw OpenTelemetry Trace Data from All neard Nodes Publicly — (File: `tracing/src/queries.rs`)

---

### Summary

`run_query_server` in `tracing/src/queries.rs` hardcodes its TCP listener to `("0.0.0.0", port)` with `CorsLayer::permissive()`, binding the trace-query HTTP server to all network interfaces. Any unprivileged client that can reach the host's IP and port can POST to `/raw_trace` or `/profile` and receive the full OpenTelemetry span corpus collected from every neard node that ships traces to this collector. There is no authentication, no IP restriction, and no configurable bind address. This is the exact structural analog of the pprof `:6060` exposure: a profiling/tracing server that should be localhost-only is instead reachable from the public internet.

---

### Finding Description

`run_query_server` is the HTTP query front-end of the `near-tracing` service, a production observability component deployed alongside forknet and benchmarking neard clusters. It is compiled into the `near-tracing` binary and started via `QuerierCmd::run`.

```rust
// tracing/src/queries.rs  line 55-66
pub async fn run_query_server(db: Database, port: u16) -> std::io::Result<()> {
    let state = Arc::new(QueryState { db });

    let app = Router::new()
        .route("/raw_trace", post(raw_trace))
        .route("/profile", post(profile))
        .with_state(state)
        .layer(CorsLayer::permissive())   // ← wildcard CORS
        .layer(CompressionLayer::new());

    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;  // ← all interfaces
    axum::serve(listener, app).await
}
```

The caller in `tracing/src/main.rs` passes only a `u16` port; there is no bind-address parameter and no way to restrict the listener to loopback without modifying the source:

```rust
// tracing/src/main.rs  line 48-53
impl QuerierCmd {
    async fn run(&self) -> anyhow::Result<()> {
        let db = Database::new(&self.mongodb_uri, false).await;
        run_query_server(db, self.query_port).await?;   // port only, no addr
        Ok(())
    }
}
```

The `docker-compose.yml` publishes the query port directly to the host:

```yaml
# tracing/docker-compose.yml  line 32-36
querier:
  build: .
  ports:
    - ${QUERY_PORT:-8080}:8080
  command: /app/near-tracing querier --mongodb-uri=...
```

The `/raw_trace` endpoint streams the entire raw `ExportTraceServiceRequest` protobuf payload (re-serialised as JSON) for any requested time window, with no limit on the window size (`// TODO: Set a limit on the duration of the request interval.`). The `/profile` endpoint converts the same corpus into a Firefox-profiler JSON profile.

The companion `CollectorCmd` also binds to `Ipv4Addr::UNSPECIFIED` (0.0.0.0) for the OTLP ingest port, meaning both the write path (collector) and the read path (querier) are exposed on all interfaces.

---

### Impact Explanation

The raw trace corpus contains:
- `service.name` attributes identifying each neard node by account ID or public key
- `thread.id` attributes revealing internal thread topology
- Span names and attributes that expose internal execution paths, timing, and state transitions of every neard actor (block production, chunk processing, state sync, etc.)
- Precise timing data that can be used to fingerprint validator behaviour and infer consensus timing

An unprivileged attacker who can reach the host on port 8080 (the default) can exfiltrate the complete internal execution trace of the entire forknet/benchmarking cluster for any time window, with no authentication required and with wildcard CORS allowing browser-based exfiltration.

**Impact: High** — full internal observability data of all connected neard nodes is readable by any network-reachable client.

---

### Likelihood Explanation

The `near-tracing` service is deployed in production forknet and benchmarking environments on GCP (as evidenced by `benchmarks/sharded-bm/bench.sh` and `pytest/tests/mocknet/sharded_bm.py`, which POST to `http://${TRACING_SERVER_EXTERNAL_IP}:8080/raw_trace`). The `EXTERNAL_IP` variable name confirms the server is reachable from outside the cluster. The default port 8080 is a well-known port that is commonly left open in cloud firewall rules. No code-level mitigation (authentication, IP allowlist, or configurable bind address) exists.

---

### Recommendation

1. Replace the hardcoded `("0.0.0.0", port)` bind with a configurable `(bind_addr, port)` parameter, defaulting to `127.0.0.1`.
2. Add a `--bind-addr` CLI flag to `QuerierCmd` (and `CollectorCmd`) so operators can explicitly opt in to external exposure.
3. Remove `CorsLayer::permissive()` or replace it with an explicit allowlist.
4. Add a maximum time-window limit to `/raw_trace` and `/profile` to prevent unbounded data exfiltration even from authorised clients.

---

### Proof of Concept

With the querier running at its default configuration:

```bash
curl -s -X POST http://<TRACING_SERVER_EXTERNAL_IP>:8080/raw_trace \
  -H 'Content-Type: application/json' \
  -d '{"start_timestamp_unix_ms": 0,
       "end_timestamp_unix_ms": 9999999999999,
       "filter": {"nodes": [], "threads": []}}' \
  | head -c 4096
```

This returns the full raw OpenTelemetry span corpus for all neard nodes, with no authentication, from any host that can reach port 8080. The `bench.sh` script in the repository uses exactly this request shape to fetch traces. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** tracing/src/queries.rs (L55-66)
```rust
pub async fn run_query_server(db: Database, port: u16) -> std::io::Result<()> {
    let state = Arc::new(QueryState { db });

    let app = Router::new()
        .route("/raw_trace", post(raw_trace))
        .route("/profile", post(profile))
        .with_state(state)
        .layer(CorsLayer::permissive())
        .layer(CompressionLayer::new());

    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;
    axum::serve(listener, app).await
```

**File:** tracing/src/main.rs (L37-53)
```rust
impl CollectorCmd {
    async fn run(&self) -> anyhow::Result<()> {
        let db = Database::new(&self.mongodb_uri, true).await;
        let collector = Collector::new(db);
        tonic::transport::Server::builder()
        .add_service(opentelemetry_proto::tonic::collector::trace::v1::trace_service_server::TraceServiceServer::new(collector))
        .serve(SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::UNSPECIFIED, self.otlp_port))).await?;
        Ok(())
    }
}

impl QuerierCmd {
    async fn run(&self) -> anyhow::Result<()> {
        let db = Database::new(&self.mongodb_uri, false).await;
        run_query_server(db, self.query_port).await?;
        Ok(())
    }
```

**File:** tracing/docker-compose.yml (L32-36)
```yaml
  querier:
    build: .
    ports:
      - ${QUERY_PORT:-8080}:8080
    command: /app/near-tracing querier --mongodb-uri=mongodb://root:insecure@mongo:27017/
```

**File:** benchmarks/sharded-bm/bench.sh (L742-745)
```shellscript
    curl -X POST http://${TRACING_SERVER_EXTERNAL_IP}:8080/raw_trace --compressed \
        -H 'Content-Type: application/json' \
        -d "{\"start_timestamp_unix_ms\": $start_time, \"end_timestamp_unix_ms\": $end_time, \"filter\": {\"nodes\": [],\"threads\": []}}" \
        -o "${trace_file}"
```
