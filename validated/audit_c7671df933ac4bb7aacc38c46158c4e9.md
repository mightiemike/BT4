Based on my investigation, this is a valid finding.

### Title
Precompile signature-verification fee is undercounted for secp256k1/ed25519/secp256r1 programs referenced only via an Address Lookup Table - (File: runtime-transaction/src/runtime_transaction/sdk_transactions.rs)

### Summary
`RuntimeTransaction<SanitizedVersionedTransaction>::try_from` computes `precompile_signature_details` (used for fee calculation) by calling `sanitized_versioned_tx.get_message().program_instructions_iter()` [1](#0-0) , which operates on the `SanitizedVersionedTransaction`/`SanitizedVersionedMessage` before Address Lookup Table (ALT) resolution has occurred. If an attacker references a precompile program (secp256k1/ed25519/secp256r1) solely via an ALT index rather than in `static_account_keys`, the program id resolution at this stage cannot see the real program id, so `PrecompileSignatureDetailsBuilder::process_instruction` classifies it as `ProgramIdStatus::NotSignature` and the transaction's cached `TransactionSignatureDetails` under-reports the number of precompile signatures.

### Finding Description
The signature/fee-relevant `precompile_signature_details` are computed once, early, in `RuntimeTransaction<SanitizedVersionedTransaction>::try_from`, using the *statically loaded* message's `program_instructions_iter()` [2](#0-1) . This happens strictly before `RuntimeTransaction<SanitizedTransaction>::try_from` resolves ALT accounts via `SanitizedTransaction::try_new(..., address_loader, ...)` [3](#0-2) . The cached result is then used for all downstream fee/signature accounting via `RuntimeTransaction<T>::num_secp256k1_signatures`/`num_ed25519_signatures`/`num_secp256r1_signatures`, which read from the cached `meta.signature_details` rather than recomputing [4](#0-3) .

`PrecompileSignatureDetailsBuilder::process_instruction` classifies a program id strictly by comparing the resolved `program_id` pubkey to the three precompile IDs [5](#0-4) [6](#0-5) . If the message's `program_instructions_iter()` at this pre-ALT stage cannot resolve the actual pubkey for an instruction whose `program_id_index` points past `static_account_keys` (i.e., into the ALT-loaded region), the resulting program id lookup will not match the precompile program, and it is classified `NotSignature`, contributing 0 to the fee-relevant signature count.

Meanwhile, actual full-verification execution of the precompile occurs later during `InvokeContext::process_message`, which uses the *fully resolved* `SanitizedTransaction`/`SVMMessage`, whose `program_instructions_iter()` resolves the pubkey correctly (including ALT-loaded accounts) and correctly detects and executes the precompile via `self.is_precompile(program_id)` / `self.process_precompile(...)` [7](#0-6) . This means the precompile's cryptographic verification work is genuinely performed by validators at execution time, but the fee charged to the sender — which is derived from the earlier, ALT-blind `signature_details` — does not reflect that cost.

### Impact Explanation
This causes the attacker to pay a fee that under-represents the actual signature-verification compute cost imposed on the network for precompile instructions referenced only via ALT, since `lamports_per_signature * num_precompile_signatures` (a component of `solana_fee::calculate_fee`) is computed from the undercounted `TransactionSignatureDetails`. This falls under the "value conservation" / incorrect fee-charging category: lamports destroyed (paid as fees) do not match the resource cost incurred, effectively letting the sender extract free precompile verification work from the cluster repeatedly at scale.

### Likelihood Explanation
Preconditions are attacker-controlled and cheap: a funded fee payer, and a deployed/extended ALT containing a precompile program id at a dynamic index (ALTs and their extension are permissionless, per `address_lookup_table::program`). Constructing a v0 transaction whose precompile instruction's `program_id_index` refers only to the ALT-loaded portion (not `static_account_keys`) is straightforward with the standard `VersionedMessage::V0` builder APIs. This is fully repeatable per-transaction and requires no special validator/leader access — an ordinary RPC/TPU client can do it.

### Recommendation
Compute (or re-validate) `precompile_signature_details` after ALT resolution, using the fully resolved `SanitizedTransaction`'s `program_instructions_iter()` (post address-loader), rather than relying solely on the pre-ALT `SanitizedVersionedTransaction`. Alternatively, in `RuntimeTransaction<SanitizedTransaction>::try_from`, recompute `signature_details` (or at least verify/refresh it) with the address-loader-resolved program ids before caching it into `meta`, ensuring the fee reflects the actual dynamically-loaded program identities.

### Proof of Concept
Integration test plan (bank/SVM level):
1. Build two v0 transactions with identical logical content: each contains one `secp256k1_program` verify instruction plus a transfer instruction.
   - Tx A: `secp256k1_program::ID` placed in `static_account_keys`.
   - Tx B: `secp256k1_program::ID` placed only in a deployed/activated ALT, referenced via `MessageAddressTableLookup` at a dynamic index, with the CompiledInstruction's `program_id_index` pointing into that dynamic range.
2. Load both into the bank (same feature set, same `lamports_per_signature`), extend/activate the ALT for Tx B, and call `RuntimeTransaction::try_create`/`Bank::verify_transaction` and then `bank.calculate_fee`/`solana_fee::calculate_fee` (or process each transaction end-to-end and diff `pre_balance - post_balance`).
3. Assert: `fee(Tx A) > fee(Tx B)` even though both transactions require identical secp256k1 verification work, and that `RuntimeTransaction::signature_details().num_secp256k1_instruction_signatures()` is 0 for Tx B versus the real count (e.g., 1) for Tx A — while `bank.process_transaction(&Tx B)` still succeeds, proving the precompile was actually executed despite being fee-exempt for that work.

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

**File:** runtime-transaction/src/runtime_transaction/sdk_transactions.rs (L113-134)
```rust
    pub fn try_from(
        statically_loaded_runtime_tx: RuntimeTransaction<SanitizedVersionedTransaction>,
        address_loader: impl AddressLoader,
        reserved_account_keys: &HashSet<Pubkey>,
    ) -> Result<Self> {
        let hash = *statically_loaded_runtime_tx.message_hash();
        let is_simple_vote_tx = statically_loaded_runtime_tx.is_simple_vote_transaction();
        let sanitized_transaction = SanitizedTransaction::try_new(
            statically_loaded_runtime_tx.transaction,
            hash,
            is_simple_vote_tx,
            address_loader,
            reserved_account_keys,
        )?;

        let tx = Self {
            transaction: sanitized_transaction,
            meta: statically_loaded_runtime_tx.meta,
        };

        Ok(tx)
    }
```

**File:** runtime-transaction/src/runtime_transaction.rs (L86-103)
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

**File:** runtime-transaction/src/signature_details.rs (L30-53)
```rust
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
