[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/aptos-vm/src/validator_txns/chunky_dkg.rs (L109-154)
```rust
        let config_resource = ConfigurationResource::fetch_config(resolver)
            .ok()
            .flatten()
            .ok_or(ExecutionFailure::Expected(
                ExpectedFailure::MissingResourceConfiguration,
            ))?;
        if metadata.epoch != config_resource.epoch() {
            return Err(ExecutionFailure::Expected(ExpectedFailure::EpochNotCurrent));
        }

        let validator_set = ValidatorSet::fetch_config(resolver).ok().flatten().ok_or(
            ExecutionFailure::Expected(ExpectedFailure::MissingResourceValidatorSet),
        )?;
        let chunky_dkg_state = ChunkyDKGState::fetch_config(resolver)
            .ok()
            .flatten()
            .ok_or(ExecutionFailure::Expected(
                ExpectedFailure::MissingResourceChunkyDKGState,
            ))?;

        let _in_progress_session_state =
            chunky_dkg_state
                .in_progress
                .as_ref()
                .ok_or(ExecutionFailure::Expected(
                    ExpectedFailure::MissingResourceInprogressChunkyDKGSession,
                ))?;

        let verifier = ValidatorVerifier::from(&validator_set);
        let authors = signature.get_signers_addresses(&verifier.get_ordered_account_addresses());

        // Check voting power.
        verifier
            .check_voting_power(authors.iter(), true)
            .map_err(|_| ExecutionFailure::Expected(ExpectedFailure::NotEnoughVotingPower))?;

        // TODO(ibalajiarun): Figure out how to verify without bcs deserialization
        let trx: AggregatedSubtranscript = bcs::from_bytes(&transcript_bytes).map_err(|_| {
            ExecutionFailure::Expected(ExpectedFailure::TranscriptDeserializationFailed)
        })?;
        if trx.dealer_epoch != metadata.epoch {
            return Err(ExecutionFailure::Expected(ExpectedFailure::EpochNotCurrent));
        }
        verifier
            .verify_multi_signatures(&trx, &signature)
            .map_err(|_| ExecutionFailure::Expected(ExpectedFailure::MultiSigVerificationFailed))?;
```

**File:** dkg/src/chunky/types.rs (L155-165)
```rust
/// Response containing the requested transcript.
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
pub struct MissingTranscriptResponse {
    pub transcript: ChunkyDKGTranscript,
}

impl MissingTranscriptResponse {
    pub fn new(transcript: ChunkyDKGTranscript) -> Self {
        Self { transcript }
    }
}
```
