### Title
Unauthenticated `citrea_haltCommitments` / `citrea_resumeCommitments` and `txpool_removeTransactionsBy*` RPC methods let any caller mutate sequencer state - (File: crates/sequencer/src/rpc.rs)

### Summary
The sequencer's public JSON-RPC surface exposes `citrea_haltCommitments`, `citrea_resumeCommitments`, `txpool_removeTransactionsByHash`, and `txpool_removeTransactionsBySender` with no authentication or authorization check, allowing any client that can reach the RPC endpoint to halt/resume the sequencer's L1 commitment submission and to evict arbitrary transactions from the mempool.

### Finding Description
The RPC server's `Auth` middleware only gates three methods — `backup_create`, `backup_validate`, `backup_info` — via `PROTECTED_METHODS`, all other methods (including sequencer-control methods) pass straight through to the handler unauthenticated: [1](#0-0) .

`SequencerRpc` defines `citrea_haltCommitments` and `citrea_resumeCommitments` as plain, unauthenticated methods that simply forward a control message to the sequencer runner: [2](#0-1) [3](#0-2) . Likewise, `txpool_removeTransactionsByHash` / `txpool_removeTransactionsBySender` directly remove pending mempool transactions with no caller verification: [4](#0-3) .

This is directly analogous to the `fil_configure` bug class described in the report: a method that mutates critical operational state (there: RPC/network config; here: commitment-posting state and mempool contents) is reachable by any unprivileged caller of the shared RPC surface, with no binding to caller identity or role. Unlike `eth_sendRawTransaction`, which is intentionally public and only inserts a signed, self-authenticating transaction into the mempool, `citrea_haltCommitments`/`citrea_resumeCommitments` and the `txpool_remove*` methods directly manipulate node-internal control flow and mempool state without requiring any signature, session, or API key — the same `Auth` layer that protects `backup_*` conspicuously does not cover them.

### Impact Explanation
Per the scan's impact taxonomy, this is a High-severity finding: "an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`." Calling `citrea_haltCommitments` stops the sequencer from submitting new commitments to L1, stalling the rollup's proof/commitment pipeline; `txpool_removeTransactionsByHash`/`BySender` lets any RPC caller censor or grief specific users' pending transactions out of the sequencer's mempool before inclusion. Both are direct, unauthenticated mutations of sequencer state reachable by any dapp/client with network access to the sequencer's RPC port — no sequencer, prover, or operator role is required to trigger them, satisfying the "unprivileged attacker" constraint.

### Likelihood Explanation
Likelihood is high: these methods require no special parameters, no proof, and no signature — an attacker only needs standard JSON-RPC access to the sequencer endpoint (the same access needed to call `eth_sendRawTransaction`) and can call `citrea_haltCommitments` or `txpool_removeTransactionsBySender` at will, repeatedly, from any origin.

### Recommendation
Add `citrea_haltCommitments`, `citrea_resumeCommitments`, `txpool_removeTransactionsByHash`, and `txpool_removeTransactionsBySender` to the `PROTECTED_METHODS` list in `crates/common/src/rpc/auth.rs` (or otherwise require an API key / operator-only authorization) so that they are subject to the same `Auth` check already applied to `backup_*` methods, consistent with their operational sensitivity.

### Proof of Concept
1. Connect to the sequencer's public RPC endpoint (the same endpoint exposing `eth_sendRawTransaction`).
2. Issue `citrea_haltCommitments` with no parameters — the request succeeds and the sequencer stops posting commitments to L1: [5](#0-4) .
3. Alternatively, issue `txpool_removeTransactionsBySender` with a victim address to purge their pending transactions from the mempool: [6](#0-5) .
4. Confirm via `Auth::call` that neither method name appears in `PROTECTED_METHODS`, so no API key check is ever performed: [1](#0-0) .

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

**File:** crates/sequencer/src/rpc.rs (L175-181)
```rust
    /// Halt sequencer commitments
    #[method(name = "citrea_haltCommitments")]
    async fn halt_commitments(&self) -> RpcResult<()>;

    /// Resume sequencer commitments
    #[method(name = "citrea_resumeCommitments")]
    async fn resume_commitments(&self) -> RpcResult<()>;
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
