[File: 'File Name: aptos-move/framework/aptos-framework/sources/chunky_dkg.move -> Scope: Critical. Unprivileged input can cause committed state to differ from the VM output that honest validators should derive.'] Can last_completed_session() (chunky_dkg.spec.move:26-33), when called by a consumer and then followed within the same transaction by a chunky_dkg::start() call for a new epoch, ever return a value that does not equal a subsequent fresh read of ChunkyDKGState.last_completed, given start() is only specified to mutate in_progress? Proof idea: call last_completed_session(),

### Citations

**File:** aptos-move/framework/aptos-framework/sources/chunky_dkg.move (L1-90)
```text
/// Chunky DKG on-chain states and helper functions.
module aptos_framework::chunky_dkg {
    use std::error;
    use std::option;
    use std::option::Option;
    use aptos_framework::event::emit;
    use aptos_framework::chunky_dkg_config::ChunkyDKGConfig;
    use aptos_framework::system_addresses;
    use aptos_framework::timestamp;
    use aptos_framework::validator_consensus_info::ValidatorConsensusInfo;
    friend aptos_framework::block;
    friend aptos_framework::reconfiguration_with_dkg;

    const ECHUNKY_DKG_IN_PROGRESS: u64 = 1;
    const ECHUNKY_DKG_NOT_IN_PROGRESS: u64 = 2;

    /// This can be considered as the public input of Chunky DKG.
    struct ChunkyDKGSessionMetadata has copy, drop, store {
        dealer_epoch: u64,
        chunky_dkg_config: ChunkyDKGConfig,
        dealer_validator_set: vector<ValidatorConsensusInfo>,
        target_validator_set: vector<ValidatorConsensusInfo>
    }

    #[event]
    struct ChunkyDKGStartEvent has drop, store {
        session_metadata: ChunkyDKGSessionMetadata,
        start_time_us: u64
    }

    /// The input and output of a Chunky DKG session.
    /// The validator set of epoch `x` works together for a Chunky DKG output for the target validator set of epoch `x+1`.
    struct ChunkyDKGSessionState has copy, store, drop {
        metadata: ChunkyDKGSessionMetadata,
        start_time_us: u64,
        aggregated_subtranscript: vector<u8>
    }

    /// The completed and in-progress Chunky DKG sessions.
    struct ChunkyDKGState has key {
        last_completed: Option<ChunkyDKGSessionState>,
        in_progress: Option<ChunkyDKGSessionState>
    }

    /// Called in genesis to initialize on-chain states.
    public fun initialize(aptos_framework: &signer) {
        system_addresses::assert_aptos_framework(aptos_framework);
        if (!exists<ChunkyDKGState>(@aptos_framework)) {
            move_to<ChunkyDKGState>(
                aptos_framework,
                ChunkyDKGState {
                    last_completed: std::option::none(),
                    in_progress: std::option::none()
                }
            );
        }
    }

    /// Mark on-chain Chunky DKG state as in-progress. Notify validators to start Chunky DKG.
    /// Idempotent for `dealer_epoch`: if a session for this epoch has already
    /// been started (in_progress or last_completed), returns without
    /// overwriting state or re-emitting an event. This enforces the
    /// invariant
