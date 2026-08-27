### Title
Precompile signature-fee undercounting via address-lookup-table-resolved program IDs - ([File: runtime-transaction/src/runtime_transaction.rs], [File: runtime-transaction/src/runtime_transaction/transaction_view.rs], [File: runtime-transaction/src/runtime_transaction/sdk_transactions.rs])

### Summary
`calculate_signature_fee` derives precompile signature counts via `SignatureCounts::from(message)`, which for the production `RuntimeTransaction<T>` type reads `num_ed25519_signatures()`/`num_secp256k1_signatures()`/`num_secp256r1_signatures()` from a **cached** `TransactionSignatureDetails` computed once at the "Statically Loaded" stage, before address-lookup-table (ALT) resolution. Because this cache is built from `program_instructions_iter()` on the pre-resolution view (which only exposes `static_account_keys()`), a v0 transaction that places `ed25519_program::ID`/`secp256k1_program::ID`/`secp256r1_program::ID` inside an ALT rather than in the static account keys can cause the fee-time count to diverge from what is later executed against the fully resolved account keys.

### Finding Description
`calculate_signature_fee` (`fee/src/lib.rs:42-56`) sums `SignatureCounts` fields, which are populated via `SignatureCounts::from(message)` (`fee/src/lib.rs:65-74`) by calling `message.num_ed25519_signatures()` etc. on the `SVMStaticMessage` trait.

For the production transaction type used throughout banking-stage/SVM (`RuntimeTransaction<T>`), these methods are explicitly **overridden** to bypass recomputation and instead pull from a cached value: [1](#0-0) 

That cache (`meta.signature_details`) is populated exactly once, at construction time, from `program_instructions_iter()` invoked on the message **before** ALT resolution:
- For sanitized-versioned transactions: `RuntimeTransaction<SanitizedVersionedTransaction>::try_from` builds `precompile_signature_details` from `sanitized_versioned_tx.get_message().program_instructions_iter()` [2](#0-1) 
- For the zero-copy transaction-view path: `from_sanitized_transaction_view` builds the same details from `transaction.program_instructions_iter()` on a `SanitizedTransactionView`, which is the pre-ALT-resolution "Statically Loaded" state; ALT resolution only happens later when converting into `ResolvedTransactionView` (`RuntimeTransaction<ResolvedTransactionView<D>>::try_new`), and that conversion does **not** recompute `signature_details` [3](#0-2) 

The actual counting logic in `PrecompileSignatureDetailsBuilder::process_instruction` matches a program id against `ed25519_program::ID`/`secp256k1_program::ID`/`secp256r1_program::ID` exactly, keyed off `program_id_index`: [4](#0-3) . At the pre-ALT-resolution stage, only `static_account_keys()` are available; an instruction whose `program_id_index` targets an entry supplied only through an address-table lookup cannot be correctly resolved to the real precompile pubkey at that point, so the builder will not recognize it as a precompile instruction and will not add to `num_ed25519_instruction_signatures` (etc.).

By contrast, actual sigverify/execution operates on the fully resolved message: precompile execution in `InvokeContext::process_message` calls `message.program_instructions_iter()` on the post-load `SVMMessage`, and `is_precompile(program_id)` there is checked against the properly resolved account keys (this same resolved `program_instructions_iter()` is also relied on by `load_transaction_accounts` to load the program account for execution) [5](#0-4) [6](#0-5) . Both leader verification (`Bank::verify_transaction`) and downstream fee assessment (`Bank::get_fee_for_message` → `solana_fee::calculate_fee` → `calculate_signature_fee`) consume the same `RuntimeTransaction`/cached `signature_details`, so the mismatch (if it manifests as under-resolution rather than a sanitize-time rejection) propagates directly to the lamports charged for the transaction.

This is a real, supported construct in the codebase: an ALT-only-resolved address (never present in `static_account_keys()`) is explicitly exercised as a legitimate transaction shape in test helpers such as `transaction_with_loaded_address_with_payer` in `unified-scheduler-logic/src/lib.rs:1588-1641`, confirming that referencing accounts purely through ALT indirection is a normal, accepted transaction pattern in this codebase — the open question is whether this is also permitted for a top-level instruction's `program_id_index`, which the available code does not conclusively rule out or in.

### Impact Explanation
If exploitable, this allows an unprivileged client to submit a v0 transaction whose ed25519/secp256k1/secp256r1 precompile instruction's `program_id_index` resolves through an ALT entry rather than a static account key. `calculate_signature_fee`/`SignatureCounts::from` would then undercount (or, depending on internal indexing behavior, potentially miscompute) the precompile signature contribution cached in `TransactionSignatureDetails`, while the leader still performs the full precompile signature verification work (`ed25519::verify`, etc.) at execution time. The result is fee accounting that does not reflect the actual verification work performed — a "precompile fee accounting bypass," corresponding to a lamport-undercharging / fee-bypass bounty category rather than a direct fund theft or consensus-halt.

### Likelihood Explanation
Preconditions are cheap and fully attacker-controlled: a funded keypair and knowledge of how to construct a v0 message with an address lookup table containing the target precompile program id as a non-signer entry, with the instruction's `program_id_index` pointing into the ALT-resolved range. No validator/operator/leader privileges are required. However, exploitability is contingent on an unverified detail: whether `program_instructions_iter()` at the pre-ALT-resolution ("Statically Loaded") stage silently mis-resolves/undercount such an instruction, versus rejecting the transaction at sanitization (e.g., if there is an implicit or explicit check requiring top-level `program_id_index` to reference only static keys). The available code confirms the architectural split (cache computed pre-resolution, consumed without recomputation) but does not show the precise low-level behavior of `program_instructions_iter()` for indices beyond `static_account_keys().len()` in the pre-resolution message types, nor whether sanitization rejects such transactions outright.

### Recommendation
- Recompute (or at least re-validate) `TransactionSignatureDetails`/`precompile_signature_details` after ALT resolution, using the fully resolved `account_keys()`, rather than relying solely on the pre-resolution cache in `RuntimeTransaction<ResolvedTransactionView<D>>`.
- Alternatively, explicitly reject (at sanitization time, before caching signature details) any top-level instruction whose `program_id_index` resolves to an ALT-loaded (non-static) account when that account id matches (or could match, post-resolution) a precompile program id — or more generally, require that all top-level program ids be static account keys, and enforce this uniformly at sanitization.
- Add an explicit unit/integration test asserting that `num_ed25519_signatures()` (and the secp256k1/secp256r1 equivalents) on a fully resolved `RuntimeTransaction` always matches the count produced by `get_precompile_signature_details` when computed against the resolved `account_keys()`, regardless of whether the precompile program id was supplied statically or via ALT.

### Proof of Concept
1. Construct a v0 `Message` where the fee payer is a funded keypair, and an `AddressLookupTableAccount` contains `solana_sdk_ids::ed25519_program::id()` as a non-signer entry (mirroring the pattern in `unified-scheduler-logic/src/lib.rs`'s `transaction_with_loaded_address_with_payer`, but resolving the program id itself through the ALT rather than an instruction account).
2. Build the top-level ed25519 precompile instruction with `program_id_index` pointing at the ALT-resolved slot (i.e., an index ≥ `static_account_keys().len()`), with valid `Ed25519SignatureOffsets` data specifying `num_signatures = N`.
3. Drive this through the same pipeline used in production: `RuntimeTransaction::<SanitizedTransactionView<_>>::try_new` (or `RuntimeTransaction::<SanitizedVersionedTransaction>::try_from`) followed by `RuntimeTransaction::<ResolvedTransactionView<_>>::try_new` with the loaded address supplied, mirroring `Bank::verify_transaction`.
4. Assert `resolved_tx.num_ed25519_signatures() == N` (matching what `precompiles::ed25519::verify` will actually check via `process_precompile`/`InvokeContext::process_message`). If the assertion fails (count is `0` or otherwise wrong), this confirms `SignatureCounts::from` / `calculate_signature_fee` undercounts relative to actual sigverify work.
5. As a control, additionally attempt bank-level execution of this transaction and confirm it is either (a) accepted and executed with the ed25519 precompile actually verifying `N` signatures while the assessed fee reflects fewer/zero signatures (confirms the bypass), or (b) rejected at sanitization (in which case the vulnerability is not reachable and the finding should be downgraded/invalidated).

### Citations

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

**File:** runtime-transaction/src/runtime_transaction/sdk_transactions.rs (L35-55)
```rust
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

**File:** runtime-transaction/src/runtime_transaction/transaction_view.rs (L68-143)
```rust
fn from_sanitized_transaction_view<D>(
    transaction: &SanitizedTransactionView<D>,
    message_hash: MessageHash,
    is_simple_vote_tx: Option<bool>,
) -> Result<CachedTransactionMeta>
where
    D: TransactionData,
{
    let message_hash = match message_hash {
        MessageHash::Precomputed(hash) => hash,
        MessageHash::Compute => VersionedMessage::hash_raw_message(transaction.message_data()),
    };
    let is_simple_vote_tx =
        is_simple_vote_tx.unwrap_or_else(|| is_simple_vote_transaction(transaction));

    let InstructionMeta {
        precompile_signature_details,
        instruction_data_len,
    } = InstructionMeta::try_new(transaction.program_instructions_iter())?;

    let signature_details = TransactionSignatureDetails::new(
        u64::from(transaction.num_required_signatures()),
        precompile_signature_details.num_secp256k1_instruction_signatures,
        precompile_signature_details.num_ed25519_instruction_signatures,
        precompile_signature_details.num_secp256r1_instruction_signatures,
    );
    let versioned_transaction_config =
        if let Some(transaction_config_view) = transaction.transaction_config() {
            // NOTE: only txv1 has `transaction_config_view`, which must have been validated for
            // SanitizedTransactionView.
            VersionedTransactionConfiguration::V1(TransactionConfiguration {
                priority_fee_lamports: transaction_config_view.priority_fee_lamports().unwrap_or(0),
                compute_unit_limit: transaction_config_view.compute_unit_limit().unwrap_or(0),
                loaded_accounts_data_size_limit: transaction_config_view
                    .loaded_accounts_data_size_limit()
                    .unwrap_or(0),
                updated_heap_bytes: transaction_config_view
                    .requested_heap_size()
                    .unwrap_or(HEAP_LENGTH as u32),
            })
        } else {
            VersionedTransactionConfiguration::LegacyAndV0(
                ComputeBudgetInstructionDetails::try_from(transaction.program_instructions_iter())?,
            )
        };

    Ok(CachedTransactionMeta {
        message_hash,
        is_simple_vote_transaction: is_simple_vote_tx,
        signature_details,
        versioned_transaction_config,
        instruction_data_len,
    })
}

impl<D: TransactionData> RuntimeTransaction<ResolvedTransactionView<D>> {
    /// Create a new `RuntimeTransaction<ResolvedTransactionView>` from a
    /// `RuntimeTransaction<SanitizedTransactionView>` that already has
    /// static metadata loaded.
    pub fn try_new(
        statically_loaded_runtime_tx: RuntimeTransaction<SanitizedTransactionView<D>>,
        loaded_addresses: Option<LoadedAddresses>,
        reserved_account_keys: &HashSet<Pubkey>,
    ) -> Result<Self> {
        let RuntimeTransaction { transaction, meta } = statically_loaded_runtime_tx;
        // transaction-view does not distinguish between different types of errors here.
        // return generic sanitize failure error here.
        // these transactions should be immediately dropped, and we generally
        // will not care about the specific error at this point.
        let transaction =
            ResolvedTransactionView::try_new(transaction, loaded_addresses, reserved_account_keys)
                .map_err(|_| TransactionError::SanitizeFailure)?;
        let tx = Self { transaction, meta };
        Ok(tx)
    }
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

**File:** svm/src/account_loader.rs (L607-618)
```rust
    for (program_id, _) in message.program_instructions_iter() {
        let Some(program_account) = account_loader.load_account(program_id) else {
            error_metrics.account_not_found += 1;
            return Err(TransactionError::ProgramAccountNotFound);
        };

        let owner_id = program_account.owner();
        if !native_loader::check_id(owner_id) && !PROGRAM_OWNERS.contains(owner_id) {
            error_metrics.invalid_program_for_execution += 1;
            return Err(TransactionError::InvalidProgramForExecution);
        }
    }
```
