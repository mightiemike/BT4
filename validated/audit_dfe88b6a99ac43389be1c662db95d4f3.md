### Title
Unauthenticated `citrea_haltCommitments` / `citrea_resumeCommitments` and `txpool_removeTransactionsBy*` RPC methods mutate sequencer state without any caller authentication - (File: `crates/sequencer/src/rpc.rs`)

### Summary
The sequencer's JSON-RPC interface exposes several state-mutating methods — `citrea_haltCommitments`, `citrea_resumeCommitments`, `txpool_removeTransactionsByHash`, and `txpool_removeTransactionsBySender` — that are not gated by any caller identity, signature, or allowance check, analogous to the reported `_withdraw` bug class where an action is performed on behalf of/against a target without verifying the caller is authorized to do so.

### Finding Description
The node-wide `Auth` RPC middleware only requires an API key for three specific methods: [1](#0-0) 

Every other registered RPC method, including all `SequencerRpc` methods, passes straight through with no authentication check: [2](#0-1) 

Among the methods left unauthenticated are `citrea_haltCommitments` and `citrea_resumeCommitments`, which directly control whether the sequencer keeps posting batch commitments to the DA layer: [3](#0-2) 

and `txpool_removeTransactionsByHash` / `txpool_removeTransactionsBySender`, which let any caller purge arbitrary users' pending transactions from the sequencer mempool by specifying someone else's `sender` address or transaction hashes, with no check that the caller owns those transactions: [4](#0-3) 

These four methods are declared in the same `SequencerRpc` trait as the legitimately public `eth_sendRawTransaction`/`citrea_sendRawDepositTransaction` methods and are registered into the shared `RpcModule` without any additional guard: [5](#0-4) 

The RPC server only applies the CORS/timeout/logging/metrics/Auth middleware stack uniformly; there is no per-method authorization layer distinguishing "operator-only" methods from public ones: [6](#0-5) 

This mirrors the reported bug class: an action that should only be performable by an authorized party (the token/asset owner in the Hats report; the sequencer operator here) is instead callable by "anyone" — the binding "caller == authorized party" is never checked before the state mutation is performed.

### Impact Explanation
`citrea_haltCommitments` mutates core sequencer behavior — an unauthenticated party can halt commitment publication to the DA layer, breaking the invariant that only the sequencer operator controls when/whether commitments (and therefore the rollup's proven state) advance. `txpool_removeTransactionsBy*` lets an unauthenticated caller purge other users' pending transactions from the sequencer mempool by specifying an arbitrary `sender`, denying service to legitimate users without their consent. Both are unauthenticated JSON-RPC calls that mutate node state and bypass `Auth`, matching the High-severity bucket "an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`."

### Likelihood Explanation
High: any client with network access to the sequencer's JSON-RPC port can invoke these methods directly with no credentials, since the `Auth` middleware's `PROTECTED_METHODS` allowlist does not include them.

### Recommendation
Add `citrea_haltCommitments`, `citrea_resumeCommitments`, `txpool_removeTransactionsByHash`, and `txpool_removeTransactionsBySender` (and any other operator-only RPC methods) to the `PROTECTED_METHODS` list in `crates/common/src/rpc/auth.rs`, or introduce a dedicated operator-role authorization layer so state-mutating administrative RPCs cannot be invoked by arbitrary callers.

### Proof of Concept
Any unauthenticated client can call:
```
POST /
{"jsonrpc":"2.0","id":1,"method":"citrea_haltCommitments","params":[]}
```
or
```
{"jsonrpc":"2.0","id":1,"method":"txpool_removeTransactionsBySender","params":["0xVictimAddress"]}
```
against the sequencer's RPC endpoint. Both requests succeed without any API key or signature, per `crates/sequencer/src/rpc.rs:407-425` and `459-474`, since neither method name appears in `PROTECTED_METHODS` (`crates/common/src/rpc/auth.rs:11`).

### Citations

**File:** crates/common/src/rpc/auth.rs (L11-17)
```rust
const PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"];

#[derive(Debug, Clone)]
pub struct Auth<S> {
    service: S,
    api_key: Option<String>,
}
```

**File:** crates/common/src/rpc/auth.rs (L31-38)
```rust
    fn call(&self, req: Request<'a>) -> Self::Future {
        let method = req.method_name();
        let service = self.service.clone();
        let api_key = self.api_key.clone().map(Value::from);

        if !PROTECTED_METHODS.contains(&method) {
            return Box::pin(service.call(req));
        }
```

**File:** crates/sequencer/src/rpc.rs (L93-200)
```rust
/// Interface definition for the sequencer RPC calls.
///
/// This trait defines all available RPC methods that can be called on the sequencer.
#[rpc(client, server)]
pub trait SequencerRpc {
    /// Submits a raw transaction to the mempool
    ///
    /// # Arguments
    /// * `data` - The raw transaction data
    ///
    /// # Returns
    /// The transaction hash
    #[method(name = "eth_sendRawTransaction")]
    async fn eth_send_raw_transaction(&self, data: Bytes) -> RpcResult<B256>;

    /// Retrieves transaction information by hash
    ///
    /// This implements the standard Ethereum JSON-RPC `eth_getTransactionByHash` method with
    /// an additional feature to query only mempool transactions.
    ///
    /// The method first checks the mempool for the transaction. If not found, it will check
    /// the blockchain state unless `mempool_only` is set to true.
    ///
    /// # Arguments
    /// * `hash` - The transaction hash
    /// * `mempool_only` - If true, only check the mempool. Default is false.
    ///    This is a Citrea-specific extension to the standard Ethereum RPC.
    ///
    /// # Returns
    /// * If the transaction is in the mempool: Returns the pending transaction details
    /// * If mempool_only is false and not in mempool: Searches the blockchain state
    /// * If not found in either location: Returns None
    ///
    /// This extended functionality allows clients to specifically query for
    /// transactions that haven't been included in a block yet.
    #[method(name = "eth_getTransactionByHash")]
    #[blocking]
    fn eth_get_transaction_by_hash(
        &self,
        hash: B256,
        mempool_only: Option<bool>,
    ) -> RpcResult<Option<Transaction>>;

    /// Submits a raw deposit transaction
    ///
    /// # Arguments
    /// * `deposit` - The raw deposit transaction data
    ///
    /// # Processing Steps
    /// 1. Creates a deposit transaction from the raw data
    /// 2. Performs an eth_call simulation with the deposit data against the bridge contract
    ///    to validate that the deposit would succeed
    /// 3. If the simulation succeeds, adds the deposit to the FIFO deposit mempool
    /// 4. If the simulation fails, returns an error
    ///
    /// This ensures deposits are valid before being accepted into the mempool.
    #[method(name = "citrea_sendRawDepositTransaction")]
    #[blocking]
    fn send_raw_deposit_transaction(&self, deposit: Bytes) -> RpcResult<()>;

    /// Retrieves the raw EIP-2718 encoded transaction by hash
    ///
    /// Same lookup logic as `eth_getTransactionByHash` but returns the EIP-2718
    /// encoded transaction bytes instead of the parsed transaction object.
    ///
    /// # Arguments
    /// * `hash` - The transaction hash
    /// * `mempool_only` - If true, only check the mempool. Default is false.
    #[method(name = "eth_getRawTransactionByHash")]
    #[blocking]
    fn eth_get_raw_transaction_by_hash(
        &self,
        hash: B256,
        mempool_only: Option<bool>,
    ) -> RpcResult<Option<Bytes>>;

    /// Forces block production in test mode
    ///
    /// This method is only available when the sequencer is running in test mode.
    #[method(name = "citrea_testPublishBlock")]
    async fn publish_test_block(&self) -> RpcResult<()>;

    /// Halt sequencer commitments
    #[method(name = "citrea_haltCommitments")]
    async fn halt_commitments(&self) -> RpcResult<()>;

    /// Resume sequencer commitments
    #[method(name = "citrea_resumeCommitments")]
    async fn resume_commitments(&self) -> RpcResult<()>;

    /// Returns the transaction pool content.
    #[method(name = "txpool_content")]
    async fn txpool_content(&self) -> RpcResult<TxpoolContent<Transaction>>;

    /// Removes transactions from the pool by hash.
    /// Returns the hashes of the removed transactions.
    #[method(name = "txpool_removeTransactionsByHash")]
    async fn txpool_remove_txs_by_hash(&self, hashes: Vec<B256>) -> RpcResult<Vec<B256>>;

    /// Removes all transactions from the pool by sender.
    /// Returns the hashes of the removed transactions.
    #[method(name = "txpool_removeTransactionsBySender")]
    async fn txpool_remove_txs_by_sender(&self, sender: Address) -> RpcResult<Vec<B256>>;

    /// Returns the sync status
    #[method(name = "eth_syncing")]
    async fn eth_syncing(&self) -> RpcResult<EthSyncStatus>;
}
```

**File:** crates/sequencer/src/rpc.rs (L407-425)
```rust
    /// Halt sequencer commitments
    async fn halt_commitments(&self) -> RpcResult<()> {
        debug!("Sequencer: citrea_haltCommitments");
        self.context
            .rpc_message_tx
            .send(SequencerRpcMessage::HaltCommitments)
            .map_err(|e| internal_rpc_error(format!("Could not send halt commitments signal: {e}")))
    }

    /// Resume sequencer commitments
    async fn resume_commitments(&self) -> RpcResult<()> {
        debug!("Sequencer: citrea_resumeCommitments");
        self.context
            .rpc_message_tx
            .send(SequencerRpcMessage::ResumeCommitments)
            .map_err(|e| {
                internal_rpc_error(format!("Could not send resume commitments signal: {e}"))
            })
    }
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

**File:** crates/common/src/rpc/server.rs (L37-45)
```rust
    let middleware = tower::ServiceBuilder::new()
        .layer(super::get_cors_layer())
        .layer(super::get_healthcheck_proxy_layer())
        .layer(TimeoutLayer::new(Duration::from_secs(rpc_config.timeout)));

    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(move |s| super::auth::Auth::new(s, rpc_config.api_key.clone()))
        .layer_fn(super::Logger)
        .layer_fn(RpcMetrics);
```
