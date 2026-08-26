[File: precompiles/src/ed25519.rs -> Scope: vote-program corruption] [Function: verify] Can an attacker place the ed25519 precompile instruction and a VoteInstruction::Authorize in the same transaction where the ed25519 instruction

### Citations

**File:** precompiles/src/ed25519.rs (L30-49)
```rust
    for i in 0..num_signatures {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(SIGNATURE_OFFSETS_START);

        // SAFETY:
        // - data[start..] is guaranteed to be >= size of Ed25519SignatureOffsets
        // - Ed25519SignatureOffsets is a POD type, so we can safely read it as an unaligned struct
        let offsets = unsafe {
            core::ptr::read_unaligned(data.as_ptr().add(start) as *const Ed25519SignatureOffsets)
        };

        // Parse out signature
        let signature = get_data_slice(
            data,
            instruction_datas,
            offsets.signature_instruction_index,
            offsets.signature_offset,
            SIGNATURE_SERIALIZED_SIZE,
        )?;
```

**File:** programs/vote/src/vote_state/mod.rs (L686-731)
```rust
pub fn authorize<S: std::hash::BuildHasher, F>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    authorized: &Pubkey,
    vote_authorize: VoteAuthorize,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
    is_vote_authorize_with_bls_enabled: bool,
    consume_pop_compute_units: F,
) -> Result<(), InstructionError>
where
    F: FnOnce() -> Result<(), InstructionError>,
{
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    match vote_authorize {
        VoteAuthorize::Voter => {
            if is_vote_authorize_with_bls_enabled && vote_state.has_bls_pubkey() {
                return Err(InstructionError::InvalidInstructionData);
            }
            let authorized_withdrawer_signer =
                verify_authorized_signer(vote_state.authorized_withdrawer(), signers).is_ok();

            vote_state.set_new_authorized_voter(
                authorized,
                clock.epoch,
                clock
                    .leader_schedule_epoch
                    .checked_add(1)
                    .ok_or(InstructionError::InvalidAccountData)?,
                None,
                |epoch_authorized_voter| {
                    // current authorized withdrawer or authorized voter must say
