[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** dkg/src/epoch_manager.rs (L297-298)
```rust
        // Create shared network sender for both DKG managers
        let network_sender = Arc::new(self.create_network_sender());
```

**File:** dkg/src/epoch_manager.rs (L404-447)
```rust
    async fn start_chunky_dkg_manager(
        &mut self,
        epoch_state: Arc<EpochState>,
        my_index: usize,
        payload: &OnChainConfigPayload<P>,
        rb: Arc<ReliableBroadcast<DKGMessage, ExponentialBackoff>>,
        network_sender: Arc<NetworkSender>,
    ) -> Result<()> {
        let ChunkyDKGState {
            in_progress: in_progress_session,
            ..
        } = payload.get::<ChunkyDKGState>().unwrap_or_default();

        let (chunky_dkg_start_event_tx, chunky_dkg_start_event_rx) =
            aptos_channel::new(QueueStyle::KLAST, 1, None);
        self.chunky_dkg_start_event_tx = Some(chunky_dkg_start_event_tx);

        let (chunky_dkg_rpc_msg_tx, chunky_dkg_rpc_msg_rx) = aptos_channel::new::<
            AccountAddress,
            (AccountAddress, IncomingRpcRequest),
        >(QueueStyle::FIFO, 100, None);
        self.chunky_dkg_rpc_msg_tx = Some(chunky_dkg_rpc_msg_tx);
        let (chunky_dkg_manager_close_tx, chunky_dkg_manager_close_rx) = oneshot::channel();
        self.chunky_dkg_manager_close_tx = Some(chunky_dkg_manager_close_tx);
        let my_pk = epoch_state
            .verifier
            .get_public_key(&self.my_addr)
            .ok_or_else(|| anyhow!("my pk not found in validator set"))?;
        let dealer_sk = self
            .key_storage
            .consensus_sk_by_pk(my_pk.clone())
            .map_err(|e| {
                anyhow!("chunky dkg new epoch handling failed with consensus sk lookup err: {e}")
            })?;
        let chunky_dkg_manager = ChunkyDKGManager::new(
            Arc::new(dealer_sk),
            Arc::new(my_pk),
            my_index,
            self.my_addr,
            epoch_state,
            self.vtxn_pool.clone(),
            rb,
            network_sender,
        );
```

**File:** dkg/src/chunky/dkg_manager/mod.rs (L850-851)
```rust
        self.rpc_handler_guards
            .insert(sender, (req_subtranscript_hash, AbortOnDrop(handle)));
```

**File:** dkg/src/chunky/dkg_manager/mod.rs (L858-865)
```rust
    async fn resolve_subtranscripts(
        sender: AccountAddress,
        req: &ChunkyDKGSubtranscriptSignatureRequest,
        received_transcripts: &Arc<RwLock<HashMap<AccountAddress, ChunkyTranscriptWithHash>>>,
        epoch_state: &Arc<EpochState>,
        dkg_config: &Arc<ChunkyDKGSession>,
        network_sender: Arc<NetworkSender>,
    ) -> Result<Vec<ChunkySubtranscript>> {
```

**File:** dkg/src/chunky/dkg_manager/mod.rs (L949-960)
```rust
                let fetcher = TranscriptFetcher::new(
                    sender,
                    req.dealer_epoch,
                    still_missing,
                    Duration::from_secs(10),
                    Arc::clone(dkg_config),
                    epoch_state.clone(),
                );
                let fetched = monitor!(
                    "chunky_dkg_transcript_fetch",
                    fetcher.run(network_sender).await
                );
```

**File:** dkg/src/chunky/dkg_manager/mod.rs (L994-1000)
```rust
    async fn aggregate_and_sign(
        subtranscripts: Vec<ChunkySubtranscript>,
        req: &ChunkyDKGSubtranscriptSignatureRequest,
        dkg_config: &ChunkyDKGSession,
        ssk: &Arc<DealerPrivateKey>,
    ) -> Result<(AggregatedSubtranscript, bls12381::Signature)> {
        let dealer_epoch = req.dealer_epoch;
```
