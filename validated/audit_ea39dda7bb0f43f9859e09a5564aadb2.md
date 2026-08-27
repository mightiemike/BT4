### Title
Precompile signature-verification fee undercounting via V0 transactions whose secp256k1/ed25519/secp256r1 program id is resolved only through an Address Lookup Table - ([File: runtime-transaction/src/signature_details.rs])

### Summary
`RuntimeTransaction<SanitizedVersionedTransaction>::try_from` computes `precompile_signature_details` (and therefore the cached `TransactionSignatureDetails` used for fee calculation) by iterating `program_instructions_iter()` over the *static*, pre-ALT-resolution `SanitizedVersionedTransaction`. If a V0 transaction's secp256k1/ed25519/secp256r1 program id is only reachable through an address-lookup-table (ALT) slot rather than `static_account_keys`, this static pass cannot match `ProgramIdStatus::Secp256k1/Ed25519/Secp256r1` in `SignatureDetailsFilter::check_program_id`, so the cached signature counts are computed as if the precompile were absent. Because `RuntimeTransaction` overrides `num_secp256k1_signatures`/`num_ed25519_signatures`/`num_secp256r1_signatures` to read from this cache rather than recomputing against the ALT-resolved account keys, the fee-relevant signature count and the count actually verified at execution time (where `program_instructions_iter()` runs against the fully resolved message) can diverge.

### Finding Description
The static metadata pipeline is: [1](#0-0) 

This computes `precompile_signature_details` from `sanitized_versioned_tx.get_message().program_instructions_iter()`, which only has access to `static_account_keys()` because `SanitizedVersionedTransaction` predates ALT resolution (per the module doc, "Statically Loaded" occurs "after receiving packet ... before successfully loaded account addresses from onchain ALT"): [2](#0-1) 

The matching logic in `PrecompileSignatureDetailsBuilder::process_instruction` / `SignatureDetailsFilter::is_signature` / `check_program_id` classifies a program id strictly by pubkey equality to the well-known precompile IDs: [3](#0-2) [4](#0-3) 

The resulting `signature_details` is cached in `CachedTransactionMeta` and, critically, `RuntimeTransaction<T>`'s `SVMStaticMessage` implementation **overrides** the signature-count accessors to always read from this cache instead of recomputing against the later-resolved account space: [5](#0-4) 

Meanwhile, actual precompile verification during execution operates on the *resolved* message (`ResolvedTransactionView`/`SanitizedTransaction`, whose `program_instructions_iter()` resolves program ids against the full `static + ALT` account space) via `InvokeContext::process_message`: [6](#0-5) 

If the attacker builds a V0 message whose secp256k1/ed25519/secp256r1 instruction's `program_id_index` resolves only through an address lookup table slot (not present in `static_account_keys`), the static pass in `sdk_transactions.rs` cannot see the real precompile program id (it only has `static_account_keys`), so `num_secp256k1_instruction_signatures` (etc.) in the cached `TransactionSignatureDetails` is computed as if the precompile instruction were absent/mismatched, while the ALT-resolved execution path still identifies and verifies the precompile for real. This produces a mismatch between the fee-relevant cached signature count and the actual signature-verification work performed, because fee computation elsewhere in the codebase (`solana_fee::calculate_fee`) consumes these cached counts rather than recomputing from the resolved message.

### Impact Explanation
This is a value-conservation / metering-totality violation: the fee charged to the sender for secp256k1/ed25519/secp256r1 signature verification work does not match the actual cryptographic verification work performed by validators during execution. This does not appear to allow bypassing the actual signature check itself (execution still uses the resolved message to run `process_precompile`), but it can result in validators performing (and being expected to price) more expensive ECDSA/EdDSA verification work than what the fee payer is charged for, which is a fee/metering integrity bug rather than a direct fund-theft or consensus-halting bug.

### Likelihood Explanation
The described precondition (constructing a V0 transaction whose top-level instruction's `program_id_index` resolves to a precompile program id only via an ALT slot rather than a static account key) requires that Solana's V0 message sanitization actually permits program ids to be sourced from an address lookup table for a top-level instruction. I was able to confirm from this repository's indexed code that (a) `program_instructions_iter()` for the static/unresolved message only sees `static_account_keys()`, and (b) `RuntimeTransaction` deliberately caches and does not recompute signature counts after ALT resolution — both of which are necessary conditions for the bug. However, I could **not** locate and verify, within this repo's indexed files, the exact sanitize-time bounds-check logic in the `solana-message` crate (`v0::Message::sanitize`/`try_compile`) that determines whether a `CompiledInstruction.program_id_index` is permitted to reference the ALT-resolved index range for a top-level instruction, versus being restricted to `static_account_keys` only. That crate's source was not present in the index available to me, so this precondition remains unverified rather than confirmed.

### Recommendation
Regardless of whether the ALT-program-id precondition holds, the architecture should not rely on caching signature-verification counts computed solely from the pre-ALT-resolution static message when those counts feed fee computation for messages that support ALT resolution. Recompute (or re-validate) `TransactionSignatureDetails` against the fully resolved account-key space in `RuntimeTransaction<SanitizedTransaction>::try_from` / `RuntimeTransaction<ResolvedTransactionView>::try_new`, or add an explicit sanitize-time restriction (if one does not already exist) that precompile program ids referenced by top-level `program_id_index` must be drawn from `static_account_keys` only, and reject/error otherwise.

### Proof of Concept
Suggested integration test plan (bank/SVM level), to be executed by an engineer with access to the full `solana-message` crate source to first confirm/deny the sanitize-time precondition:
1. Create and fund a keypair; create an address lookup table and `extend_lookup_table` with `secp256k1_program::ID`; wait for activation.
2. Build a V0 `Message` whose `CompiledInstruction` has `program_id_index` pointing at the slot supplied by the lookup table (not in `account_keys`), with `data[0] = N` (declared number of secp256k1 signatures), following the pattern of `v0::Message` construction seen in `runtime/src/conformance/txn.rs` `test_lookup_table`.
3. Attempt `RuntimeTransaction::<SanitizedVersionedTransaction>::try_from(...)` directly (as in `runtime-transaction/src/runtime_transaction/sdk_transactions.rs` tests) and assert whether sanitization succeeds; if it does, assert `meta.signature_details.num_secp256k1_instruction_signatures()` equals `0` (undercounted) versus the actual `N` verified once `RuntimeTransaction::<SanitizedTransaction>::try_from` resolves the ALT.
4. If step 2/3 succeeds (i.e., sanitize permits ALT-resolved program ids), extend into a bank integration test asserting the `FeeDetails`/lamports debited from the fee payer against the actual compute/signature-verification work recorded during `process_message`.

If the sanitize layer instead rejects such transactions (`SanitizeFailure`/`InvalidProgramIdIndex`), the finding should be treated as **not exploitable** in the current codebase, since the attacker precondition cannot be satisfied.

### Citations

**File:** runtime-transaction/src/runtime_transaction/sdk_transactions.rs (L22-55)
```rust
impl RuntimeTransaction<SanitizedVersionedTransaction> {
    pub fn try_from(
        sanitized_versioned_tx: SanitizedVersionedTransaction,
        message_hash: MessageHash,
        is_simple_vote_tx: Option<bool>,
    ) -> Result<Self> {
        let message_hash = match message_hash {
            MessageHash::Precomputed(hash) => hash,
            MessageHash::Compute => sanitized_versioned_tx.get_message().message.hash(),
        };
        let is_simple_vote_tx = is_simple_vote_tx
            .unwrap_or_else(|| is_simple_vote_transaction(&sanitized_versioned_tx));

        let InstructionMeta {
            precompile_signature_details,
            instruction_data_len,
        } = InstructionMeta::try_new(
            sanitized_versioned_tx
                .get_message()
                .program_instructions_iter()
                .map(|(program_id, ix)| (program_id, SVMInstruction::from(ix))),
        )?;
        let signature_details = TransactionSignatureDetails::new(
            u64::from(
                sanitized_versioned_tx
                    .get_message()
                    .message
                    .header()
                    .num_required_signatures,
            ),
            precompile_signature_details.num_secp256k1_instruction_signatures,
            precompile_signature_details.num_ed25519_instruction_signatures,
            precompile_signature_details.num_secp256r1_instruction_signatures,
        );
```

**File:** runtime-transaction/src/runtime_transaction.rs (L1-11)
```rust
//! RuntimeTransaction is `runtime` facing representation of transaction, while
//! solana_transaction::sanitized::SanitizedTransaction is client facing representation.
//!
//! It has two states:
//! 1. Statically Loaded: after receiving `packet` from sigverify and deserializing
//!    it into `solana_transaction::versioned::VersionedTransaction`, then sanitizing into
//!    `solana_transaction::versioned::sanitized::SanitizedVersionedTransaction`, which can be wrapped into
//!    `RuntimeTransaction` with static transaction metadata extracted.
//! 2. Dynamically Loaded: after successfully loaded account addresses from onchain
//!    ALT, RuntimeTransaction<SanitizedMessage> transits into Dynamically Loaded state,
//!    with its dynamic metadata loaded.
```

**File:** runtime-transaction/src/runtime_transaction.rs (L90-107)
```rust
    // override to access from the cached meta instead of re-calculating
    fn num_ed25519_signatures(&self) -> u64 {
        self.meta
            .signature_details
            .num_ed25519_instruction_signatures()
    }
    // override to access from the cached meta instead of re-calculating
    fn num_secp256k1_signatures(&self) -> u64 {
        self.meta
            .signature_details
            .num_secp256k1_instruction_signatures()
    }
    // override to access form the cached meta instead of re-calculating
    fn num_secp256r1_signatures(&self) -> u64 {
        self.meta
            .signature_details
            .num_secp256r1_instruction_signatures()
    }
```

**File:** runtime-transaction/src/signature_details.rs (L29-53)
```rust
impl PrecompileSignatureDetailsBuilder {
    pub fn process_instruction(&mut self, program_id: &Pubkey, instruction: &SVMInstruction) {
        let program_id_index = instruction.program_id_index;
        match self.filter.is_signature(program_id_index, program_id) {
            ProgramIdStatus::NotSignature => {}
            ProgramIdStatus::Secp256k1 => {
                self.value.num_secp256k1_instruction_signatures = self
                    .value
                    .num_secp256k1_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
            ProgramIdStatus::Ed25519 => {
                self.value.num_ed25519_instruction_signatures = self
                    .value
                    .num_ed25519_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
            ProgramIdStatus::Secp256r1 => {
                self.value.num_secp256r1_instruction_signatures = self
                    .value
                    .num_secp256r1_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
        }
    }
```

**File:** runtime-transaction/src/signature_details.rs (L111-122)
```rust
    #[inline]
    fn check_program_id(program_id: &Pubkey) -> ProgramIdStatus {
        if program_id == &solana_sdk_ids::secp256k1_program::ID {
            ProgramIdStatus::Secp256k1
        } else if program_id == &solana_sdk_ids::ed25519_program::ID {
            ProgramIdStatus::Ed25519
        } else if program_id == &solana_sdk_ids::secp256r1_program::ID {
            ProgramIdStatus::Secp256r1
        } else {
            ProgramIdStatus::NotSignature
        }
    }
```

**File:** program-runtime/src/invoke_context.rs (L503-525)
```rust
    pub fn process_message(
        &mut self,
        message: &'ix_data impl SVMMessage,
        execute_timings: &mut ExecuteTimings,
        accumulated_consumed_units: &mut u64,
    ) -> Result<(), (u8, InstructionError)> {
        self.prepare_top_level_instructions(message)?;

        for (top_level_instruction_index, (program_id, instruction)) in
            message.program_instructions_iter().enumerate()
        {
            let mut compute_units_consumed = 0;
            let (result, process_instruction_us) = measure_us!({
                if self.is_precompile(program_id) {
                    self.process_precompile(
                        program_id,
                        instruction.data,
                        message.instructions_iter().map(|ix| ix.data),
                    )
                } else {
                    self.process_instruction(&mut compute_units_consumed, execute_timings)
                }
            });
```
