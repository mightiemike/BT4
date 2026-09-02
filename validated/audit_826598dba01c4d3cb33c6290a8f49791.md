### Title
Incomplete `PROTECTED_METHODS` allowlist lets unauthenticated callers invoke sensitive batch-prover/sequencer RPC methods that mutate node state - (File: `crates/common/src/rpc/auth.rs`)

### Summary
The JSON-RPC `Auth` middleware only requires the configured `api_key` for three hard-coded method names (`backup_create`, `backup_validate`, `backup_info`). All other RPC methods, including state-mutating administrative methods exposed by the batch prover (`batchProver_setCommitments`, `batchProver_prove`, `batchProver_submitFakeProof`, `batchProver_pauseProving`) and the sequencer (`citrea_haltCommitments`, `citrea_resumeCommitments`), bypass authentication entirely and are callable by anyone who can reach the RPC port.

### Finding Description
`Auth::call` gates access using a fixed allowlist: [1](#0-0) 

Any method not in `PROTECTED_METHODS` is forwarded to the underlying service without checking `api_key`, regardless of whether an `api_key` is configured for the node. This middleware is the only authentication layer wired into the RPC server: [2](#0-1) 

Meanwhile, the batch prover exposes RPC methods that directly mutate proving-relevant node state without any additional access control of their own, e.g. `setCommitments` ("Manually set commitments. It overrides the commitment already if exists"), `prove`, `submitFakeProof`, and `pauseProving`: [3](#0-2) 

Similarly, the sequencer exposes `citrea_haltCommitments` / `citrea_resumeCommitments`, which mutate the sequencer's commitment-publishing state via an unauthenticated message channel: [4](#0-3) 

This is the same bug class as the reported `CsFeeOracle.initialize()` issue: a state-mutating administrative entry point that is reachable by any unprivileged caller because the access-control mechanism (there: missing modifier; here: an incomplete allowlist in `Auth`) does not actually cover it.

### Impact Explanation
`batchProver_setCommitments` allows overwriting the sequencer commitments the batch prover ledger uses as the basis for generating batch proofs, without any authentication, even on deployments that configure `api_key` specifically to lock down sensitive RPC surface. This is an unauthenticated JSON-RPC call that mutates node state, which can corrupt or manipulate the batch prover's view of which commitments to prove over, and `citrea_haltCommitments`/`citrea_resumeCommitments` allow unauthenticated control over whether the sequencer publishes new commitments at all. Both are administrative controls the operator believes are protected by the `Auth` middleware/`api_key`, but the hard-coded 3-item allowlist means they are not.

### Likelihood Explanation
High: no attacker privilege is required beyond network access to the node's RPC endpoint (which is the exact same access level needed to call any other public JSON-RPC method). The vulnerability is purely a code defect (incomplete allowlist), not a misconfiguration—operators cannot fix it by configuring `api_key` correctly, since the check is hard-coded to three method names.

### Recommendation
Invert the protection model: default to requiring the `api_key` for all RPC methods that mutate state (or maintain an explicit allowlist of *safe/public* read-only methods), rather than an allowlist of protected methods that must be manually kept in sync as new administrative RPC methods are added. At minimum, add `batchProver_setCommitments`, `batchProver_prove`, `batchProver_submitFakeProof`, `batchProver_pauseProving`, `citrea_haltCommitments`, and `citrea_resumeCommitments` to `PROTECTED_METHODS`.

### Proof of Concept
1. Deploy a batch-prover node with `RPC_API_KEY` set (intending to protect privileged RPC methods).
2. As an unauthenticated attacker with network access to the RPC port, call `batchProver_setCommitments` with an empty/`api_key` param omitted:
   ```
   curl -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"batchProver_setCommitments","params":[[...]]}' \
     http://<node>:<rpc_port>
   ```
3. Observe the call succeeds (per `Auth::call`, since `"batchProver_setCommitments"` is not in `PROTECTED_METHODS`, the request is forwarded directly to the service without an API-key check), overriding the prover's stored commitments.
4. Repeat with `citrea_haltCommitments` against a sequencer node to halt commitment publishing without any credential.

### Citations

**File:** crates/common/src/rpc/auth.rs (L11-47)
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

        let Some(api_key) = api_key else {
            return Box::pin(async move {
                MethodResponse::error(
                    req.id().clone(),
                    ErrorObjectOwned::owned(401, "Cannot access protected method", None::<String>),
                )
            });
        };
```

**File:** crates/common/src/rpc/server.rs (L42-45)
```rust
    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(move |s| super::auth::Auth::new(s, rpc_config.api_key.clone()))
        .layer_fn(super::Logger)
        .layer_fn(RpcMetrics);
```

**File:** crates/batch-prover/src/rpc.rs (L172-209)
```rust
    /// Manually set commitments. It overrides the commitment already if exists, so use with caution.
    ///
    /// # Arguments
    /// * `commitments` - A vector of sequencer commitments to be set.
    #[method(name = "setCommitments")]
    async fn set_commitments(&self, commitments: Vec<SequencerCommitmentRpcParam>)
        -> RpcResult<()>;

    /// Manually signal proving. This rpc triggers a proving signal with the difference that sampling will be ignored.
    ///
    /// # Arguments
    /// * `mode` - The partition mode to use for proving.
    #[method(name = "prove")]
    async fn prove(&self, mode: PartitionMode) -> RpcResult<Vec<Uuid>>;

    /// Simulate proving by collecting output from the execution in native, and submit the fake proof to DA.
    ///
    /// # Arguments
    /// * `index_start` - The starting index of the commitment range to submit a fake proof for.
    /// * `index_end` - The ending index of the commitment range to submit a fake proof for.
    /// Important caveats regarding the arguments:
    /// - `index_start` must be greater than 1, as the first commitment index requires special handling.
    /// - `index_end` must be greater than or equal to `index_start`.
    /// - The range is inclusive, meaning both `index_start` and `index_end` are included in the proof.
    /// - The previous index to `index_start` must exist in the ledger database if `index_start` is greater than 1.
    ///
    /// # Returns
    /// A `BatchProofResponse` containing the L1 transaction ID, proof, and proof output.
    #[method(name = "submitFakeProof")]
    async fn submit_fake_proof(
        &self,
        index_start: u32,
        index_end: u32,
    ) -> RpcResult<BatchProofResponse>;

    /// Stop further proving jobs to be spawned. Existing jobs will continue.
    #[method(name = "pauseProving")]
    async fn pause_proving(&self) -> RpcResult<()>;
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
