[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** state-sync/state-sync-driver/src/storage_synchronizer.rs (L973-983)
```rust
                Err(error) => {
                    let error =
                        format!("Failed to commit {} value chunk! Error: {:?}", noun, error);
                    send_storage_synchronizer_error(
                        error_notification_sender.clone(),
                        notification_id,
                        error,
                    )
                    .await;
                },
            }
```

**File:** state-sync/state-sync-driver/src/storage_synchronizer.rs (L1017-1067)
```rust
    let receiver = async move {
        let version = target_ledger_info.ledger_info().version();
        let mut snapshot_receiver: Option<Box<dyn StateSnapshotReceiver<StateKey, StateValue>>> =
            None;

        while let Some(storage_data_chunk) = snapshot_listener.next().await {
            let _timer =
                metrics::start_timer(&metrics::STORAGE_SYNCHRONIZER_LATENCIES, timer_label);

            // Create the receiver lazily on the first chunk, so a failure (e.g.
            // the native-position backend not being attached locally) surfaces as
            // a recoverable error notification tied to the chunk, rather than
            // panicking the receiver task.
            if snapshot_receiver.is_none() {
                match storage
                    .writer
                    .get_state_snapshot_receiver(version, expected_root, kind)
                {
                    Ok(new_receiver) => snapshot_receiver = Some(new_receiver),
                    Err(error) => {
                        if let StorageDataChunk::States(notification_id, _) = &storage_data_chunk {
                            send_storage_synchronizer_error(
                                error_notification_sender.clone(),
                                *notification_id,
                                format!(
                                    "Failed to initialize the {:?} snapshot receiver! Error: {:?}",
                                    kind, error
                                ),
                            )
                            .await;
                        }
                        decrement_pending_data_chunks(pending_data_chunks.clone());
                        return;
                    },
                }
            }

            match apply_snapshot_chunk(
                snapshot_receiver
                    .as_mut()
                    .expect("The snapshot receiver was initialized above!"),
                storage_data_chunk,
                kind,
                &metadata_storage,
                &target_ledger_info,
                &error_notification_sender,
                &pending_data_chunks,
                version,
            )
            .await
            {
```

**File:** storage/jellyfish-merkle/src/restore/mod.rs (L348-351)
```rust
    /// Verifies a chunk and stages frozen nodes in memory; call [`Self::commit_chunk`] to
    /// flush. On error, in-memory state is partially updated — abort the restore rather
    /// than calling again.
    pub fn verify_chunk(
```
