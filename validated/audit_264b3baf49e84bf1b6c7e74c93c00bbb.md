[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** dkg/src/dkg_manager/mod.rs (L216-219)
```rust
            InnerState::NotStarted => {},
            InnerState::InProgress { abort_handle, .. } => {
                abort_handle.abort();
            },
```

**File:** dkg/src/dkg_manager/mod.rs (L379-418)
```rust
        self.state = match std::mem::take(&mut self.state) {
            InnerState::InProgress {
                start_time,
                my_transcript,
                ..
            } => {
                let agg_transcript_ready_time = duration_since_epoch();
                let secs_since_dkg_start =
                    agg_transcript_ready_time.as_secs_f64() - start_time.as_secs_f64();
                DKG_STAGE_SECONDS
                    .with_label_values(&[self.my_addr.to_hex().as_str(), "agg_transcript_ready"])
                    .observe(secs_since_dkg_start);

                let txn = ValidatorTransaction::DKGResult(DKGTranscript {
                    metadata: DKGTranscriptMetadata {
                        epoch: self.epoch_state.epoch,
                        author: self.my_addr,
                    },
                    transcript_bytes: bcs::to_bytes(&agg_trx)
                        .map_err(|e| anyhow!("transcript serialization error: {e}"))?,
                });
                let vtxn_guard = self.vtxn_pool.put(
                    Topic::DKG,
                    Arc::new(txn),
                    Some(self.pull_notification_tx.clone()),
                );
                info!(
                    epoch = self.epoch_state.epoch,
                    my_addr = self.my_addr,
                    "[DKG] aggregated transcript put into vtxn pool."
                );
                InnerState::Finished {
                    vtxn_guard,
                    start_time,
                    my_transcript,
                    proposed: false,
                }
            },
            _ => bail!("[DKG] aggregated transcript only expected during DKG"),
        };
```
