### Title
`on_insert_block_error` can propagate a fatal error before marking the block invalid, mirroring the vault's revert-before-blacklist bug - (File: `crates/engine/tree/src/tree/mod.rs`)

### Summary
`on_insert_block_error` is the engine's single "safety" path for turning a block-validation failure into an `INVALID` `PayloadStatus` and recording the offending hash in the invalid-header cache. Exactly like `Vault.blacklistProtocol`, which tries to perform a secondary, fallible operation (`withdrawFromProtocol`) *before* it can complete the safety action (blacklisting), `on_insert_block_error` tries to compute `latest_valid_hash_for_invalid_payload` and propagates its error with `?` *before* it reaches `self.state.invalid_headers.insert(...)`.

### Finding Description [1](#0-0) 

```
fn on_insert_block_error(...) -> Result<PayloadStatus, InsertBlockProcessingError> {
    let (block, error) = error.split();
    let validation_err = error.ensure_validation_error()?;
    ...
    let latest_valid_hash =
        if matches!(&validation_err, InsertBlockValidationError::BlockAccessListDecode(_)) {
            None
        } else {
            self.latest_valid_hash_for_invalid_payload(block.parent_hash())
                .map_err(InsertBlockFatalError::from)?
        };
    ...
    self.state.invalid_headers.insert(block.block_with_parent());
    ...
    Ok(PayloadStatus::new(PayloadStatusEnum::Invalid{...}, latest_valid_hash))
}
```

`latest_valid_hash_for_invalid_payload` walks the parent chain (via the provider/tree state) to find the latest valid ancestor hash. If that lookup fails - e.g. a provider error while resolving parent headers - the `?` on `.map_err(InsertBlockFatalError::from)?` aborts the whole function *before* `self.state.invalid_headers.insert(block.block_with_parent())` runs and before any `PayloadStatus::Invalid` is ever constructed.

This is functionally the same defect as the Vault report: the "emergency"/safety action (mark block invalid / blacklist protocol) is entangled with a secondary fallible sub-operation (compute latest valid hash / withdraw underlying balance) that runs *before* the safety bookkeeping. If that sub-operation fails, the safety action never completes.

Notably, the engine authors already recognized and patched this exact failure mode in the sibling function `check_invalid_ancestor`: [2](#0-1) 
which falls back to `PayloadStatusEnum::Invalid` without a `latest_valid_hash` when `prepare_invalid_response` errors, instead of propagating a fatal error. `on_insert_block_error`, which is the primary path exercised on `engine_newPayload` validation failures, does not have this same graceful fallback and instead turns a possibly transient provider error into an `InsertBlockFatalError`.

### Impact Explanation
When `latest_valid_hash_for_invalid_payload` errors (e.g. due to a transient provider/database error while walking ancestors), the block that should be classified `INVALID` is never inserted into `invalid_headers`, and the consumer of `on_insert_block_error` receives a fatal error instead of a normal `PayloadStatus`. This maps to the "wrong `latest_valid_hash` stalling nodes until manual intervention" / "reth-built... valid chain marked invalid" class of High-impact issues: the CL never gets a deterministic `INVALID` response for the bad payload, and the offending block/head is not cached as invalid, so the same bad block can be resubmitted and repeatedly retrigger the same fatal-error path, stalling the engine loop instead of cleanly rejecting the payload.

### Likelihood Explanation
Likelihood is lower than a straightforward consensus split because it requires the ancestor/latest-valid-hash lookup itself to fail (e.g., transient provider I/O error, missing header during a reorg/prune race, or overlay-state inconsistency) at the same time a block fails post-execution/consensus validation. This is a plausible operational condition (not attacker-controlled) but not guaranteed on every invalid block, unlike the on-chain-only Vault scenario where any protocol hack/pause reliably breaks `withdrawFromProtocol`.

### Recommendation
Decouple "mark block invalid" from "compute latest valid hash", mirroring the fix already applied in `check_invalid_ancestor`: insert into `invalid_headers` and build the `Invalid` `PayloadStatus` first, and if `latest_valid_hash_for_invalid_payload` fails, fall back to returning `Invalid` with `latest_valid_hash: None` (logging the lookup failure) rather than surfacing an `InsertBlockFatalError` that skips cache insertion entirely.

### Proof of Concept
Not independently reproducible from the index alone; the trigger condition (a transient error from `latest_valid_hash_for_invalid_payload`, e.g. provider I/O failure while resolving the parent chain) could not be fully exercised with the available read-only tools. The control-flow evidence (the `?` before `invalid_headers.insert`, contrasted with the graceful fallback already present in `check_invalid_ancestor`) is cited above; a Devin session with full repo/test access would be needed to construct a concrete failing `latest_valid_hash_for_invalid_payload` call and confirm the resulting fatal-error bypass of `invalid_headers.insert`.

### Citations

**File:** crates/engine/tree/src/tree/mod.rs (L2526-2541)
```rust
    fn check_invalid_ancestor(&mut self, head: B256) -> ProviderResult<Option<PayloadStatus>> {
        // check if the head was previously marked as invalid
        let Some(header) = self.state.invalid_headers.get(&head) else { return Ok(None) };

        // Try to prepare invalid response, but handle errors gracefully
        match self.prepare_invalid_response(header.parent) {
            Ok(status) => Ok(Some(status)),
            Err(err) => {
                debug!(target: "engine::tree", %err, "Failed to prepare invalid response for ancestor check");
                // Return a basic invalid status without latest valid hash
                Ok(Some(PayloadStatus::from_status(PayloadStatusEnum::Invalid {
                    validation_error: PayloadValidationError::LinksToRejectedPayload.to_string(),
                })))
            }
        }
    }
```

**File:** crates/engine/tree/src/tree/mod.rs (L3176-3229)
```rust
    fn on_insert_block_error(
        &mut self,
        error: InsertBlockError<N::Block>,
    ) -> Result<PayloadStatus, InsertBlockProcessingError> {
        let (block, error) = error.split();

        let validation_err = error.ensure_validation_error()?;

        // If the error was due to an invalid payload, the payload is added to the
        // invalid headers cache and `Ok` with [PayloadStatusEnum::Invalid] is
        // returned.
        warn!(
            target: "engine::tree",
            invalid_hash=%block.hash(),
            invalid_number=block.number(),
            %validation_err,
            "Invalid block error on new payload",
        );
        // The Amsterdam Engine API requires `latestValidHash: null` for an undecodable BAL.
        // <https://github.com/ethereum/execution-apis/blob/df75e230befef0de56ee8833322ed714bacb479c/src/engine/amsterdam.md?plain=1#L129>
        let latest_valid_hash =
            if matches!(&validation_err, InsertBlockValidationError::BlockAccessListDecode(_)) {
                None
            } else {
                self.latest_valid_hash_for_invalid_payload(block.parent_hash())
                    .map_err(InsertBlockFatalError::from)?
            };

        // keep track of the invalid header unless the consensus impl considers it transient
        let is_transient = match &validation_err {
            InsertBlockValidationError::Consensus(err) => self.consensus.is_transient_error(err),
            _ => false,
        };
        if is_transient {
            warn!(
                target: "engine::tree",
                invalid_hash=%block.hash(),
                invalid_number=block.number(),
                %validation_err,
                "Skipping invalid header cache insert for transient validation error",
            );
        } else {
            self.state.invalid_headers.insert(block.block_with_parent());
        }
        self.emit_event(EngineApiEvent::BeaconConsensus(ConsensusEngineEvent::InvalidBlock {
            block: Box::new(block),
            error: validation_err.to_string(),
        }));

        Ok(PayloadStatus::new(
            PayloadStatusEnum::Invalid { validation_error: validation_err.to_string() },
            latest_valid_hash,
        ))
    }
```
