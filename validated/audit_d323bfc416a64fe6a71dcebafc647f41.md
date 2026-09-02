### Title
Unauthenticated `txpool_removeTransactionsByHash` / `txpool_removeTransactionsBySender` allow any caller to censor other users' pending transactions - (File: crates/sequencer/src/rpc.rs)

### Summary
The sequencer exposes two JSON-RPC methods, `txpool_removeTransactionsByHash` and `txpool_removeTransactionsBySender`, that mutate the shared transaction mempool by removing any pending or queued transaction regardless of who submitted it or who is calling the RPC. Neither method checks that the caller is the transaction's original sender, an authenticated operator, or any other authorized party.

### Finding Description
The sequencer's RPC auth middleware only protects three method names — `backup_create`, `backup_validate`, `backup_info` — via an API-key check: [1](#0-0) 

All other registered RPC methods, including the sequencer's `txpool_removeTransactionsByHash` and `txpool_removeTransactionsBySender`, pass straight through this middleware with no identity or ownership check: [2](#0-1) 

The server implementations directly forward the caller-supplied `hashes` or `sender` to the mempool without validating that the caller has any relationship to those transactions: [3](#0-2) 

Because the sequencer's RPC server is started with the same generic `start_rpc_server` used for all node types (only gated by the API-key `Auth` layer for the three protected methods), any unprivileged, unauthenticated network client that can reach the sequencer's public RPC port can call these two methods: [4](#0-3) 

Contrasting this with the analog in the external report: there, an unprivileged party escalated *their own* granted permission scope via an API endpoint that failed to re-check the bound established by the original grant. Here the binding that should exist — "only the originating sender (or an authorized administrator) may remove a pending transaction from the mempool" — does not exist at all: the RPC method performs the mutation for *any* caller against *any* other user's transactions, with zero binding check.

### Impact Explanation
Any anonymous caller with network access to a sequencer's RPC endpoint can repeatedly call `txpool_removeTransactionsBySender(victim_address)` (or by specific hash) to evict pending transactions belonging to arbitrary users from the sequencer's mempool before they are included in an L2 block. This is a mutation of node state via an unauthenticated JSON-RPC call, matching the "High" impact bucket ("an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`"). Effects include selective censorship/griefing of specific users' transactions (e.g., preventing a competitor's arbitrage/liquidation tx, or repeatedly stalling a victim's withdrawal-related transaction from ever being sequenced) without requiring any sequencer, prover, or node-operator privilege, and without needing a signature from the transaction's original sender.

### Likelihood Explanation
High likelihood: the methods require no authentication, no proof of ownership of the target transactions, and are trivially discoverable via the RPC method list (`SequencerRpc` trait). Any party who can reach the sequencer's RPC (which is generally intended to accept public `eth_sendRawTransaction` submissions) can invoke these calls with a single unauthenticated JSON-RPC request.

### Recommendation
Add `txpool_removeTransactionsByHash` and `txpool_removeTransactionsBySender` to a protected/authenticated method set (e.g., extend `PROTECTED_METHODS` in `crates/common/src/rpc/auth.rs`, or add per-method authorization requiring the caller to prove control of the sender address/transactions being removed), and/or restrict these administrative txpool-management RPCs to a local/admin-only RPC listener rather than the publicly reachable sequencer RPC endpoint.

### Proof of Concept
1. Start a Citrea sequencer node with the default RPC configuration (no additional network restrictions beyond the built-in `Auth` middleware).
2. As `userA`, submit a valid transaction via `eth_sendRawTransaction`; note it now sits in the sequencer mempool pending inclusion.
3. As an unrelated, unauthenticated attacker, call:
```
curl http://<sequencer-host>:<port> -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"txpool_removeTransactionsBySender","params":["<userA_address>"]}'
```
4. Observe that `userA`'s transaction is removed from the mempool (confirmed via `txpool_content` or `eth_getTransactionByHash` returning `None`) even though the attacker never submitted, signed, or otherwise controlled it — demonstrating an unauthenticated mutation of node/mempool state that no legitimate binding permits.

### Citations

**File:** crates/common/src/rpc/auth.rs (L11-38)
```rust
const PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"];

#[derive(Debug, Clone)]
pub struct Auth<S> {
    service: S,
    api_key: Option<String>,
}

impl<S> Auth<S> {
    pub fn new(service: S, api_key: Option<String>) -> Self {
        Self { service, api_key }
    }
}

impl<'a, S> RpcServiceT<'a> for Auth<S>
where
    S: RpcServiceT<'a> + Send + Sync + Clone + 'a,
{
    type Future = BoxFuture<'a, MethodResponse>;

    fn call(&self, req: Request<'a>) -> Self::Future {
        let method = req.method_name();
        let service = self.service.clone();
        let api_key = self.api_key.clone().map(Value::from);

        if !PROTECTED_METHODS.contains(&method) {
            return Box::pin(service.call(req));
        }
```

**File:** crates/sequencer/src/rpc.rs (L187-195)
```rust
    /// Removes transactions from the pool by hash.
    /// Returns the hashes of the removed transactions.
    #[method(name = "txpool_removeTransactionsByHash")]
    async fn txpool_remove_txs_by_hash(&self, hashes: Vec<B256>) -> RpcResult<Vec<B256>>;

    /// Removes all transactions from the pool by sender.
    /// Returns the hashes of the removed transactions.
    #[method(name = "txpool_removeTransactionsBySender")]
    async fn txpool_remove_txs_by_sender(&self, sender: Address) -> RpcResult<Vec<B256>>;
```

**File:** crates/sequencer/src/rpc.rs (L459-474)
```rust
    /// Removes transactions from the pool by hash.
    async fn txpool_remove_txs_by_hash(&self, hashes: Vec<B256>) -> RpcResult<Vec<B256>> {
        let removed_txs = self
            .context
            .mempool
            .remove_transactions_and_descendants(hashes);
        let removed_hashes: Vec<B256> = removed_txs.iter().map(|tx| *tx.hash()).collect();
        Ok(removed_hashes)
    }

    /// Removes all transactions from the pool by sender.
    async fn txpool_remove_txs_by_sender(&self, sender: Address) -> RpcResult<Vec<B256>> {
        let removed_txs = self.context.mempool.remove_transactions_by_sender(sender);
        let removed_hashes: Vec<B256> = removed_txs.iter().map(|tx| *tx.hash()).collect();
        Ok(removed_hashes)
    }
```

**File:** crates/common/src/rpc/server.rs (L37-58)
```rust
    let middleware = tower::ServiceBuilder::new()
        .layer(super::get_cors_layer())
        .layer(super::get_healthcheck_proxy_layer())
        .layer(TimeoutLayer::new(Duration::from_secs(rpc_config.timeout)));

    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(move |s| super::auth::Auth::new(s, rpc_config.api_key.clone()))
        .layer_fn(super::Logger)
        .layer_fn(RpcMetrics);

    task_executor.spawn_with_signal(move |cancellation_token| {
        async move {
            let server = ServerBuilder::default()
                .max_connections(max_connections)
                .max_subscriptions_per_connection(max_subscriptions_per_connection)
                .max_request_body_size(max_request_body_size)
                .max_response_body_size(max_response_body_size)
                .set_batch_request_config(BatchRequestConfig::Limit(batch_requests_limit))
                .set_http_middleware(middleware)
                .set_rpc_middleware(rpc_middleware)
                .build([listen_address].as_ref())
                .await;
```
