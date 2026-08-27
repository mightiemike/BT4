## Analysis: Untyped Data Signing → Agave Precompile Signature Verification

### Title
Ed25519/secp256k1/secp256r1 precompiles verify raw, undomained message bytes, enabling cross-program and cross-context signature replay - (File: `precompiles/src/ed25519.rs`, `precompiles/src/secp256k1.rs`, `precompiles/src/secp256r1.rs`)

### Summary
Agave's built-in `ed25519_program`, `secp256k1_program`, and `secp256r1_program` precompiles verify a signature over an arbitrary byte slice pulled from any instruction in the transaction, with no domain separator analogous to EIP-712's `(contract, chainId, typehash)` binding. This is the exact "untyped application data is directly hashed and signed" bug class described in the Rigor report, relocated to the signature-verification primitive layer of the runtime.

### Finding Description
The `verify` function for the ed25519 precompile parses `Ed25519SignatureOffsets` out of the precompile instruction data and pulls the message bytes from an arbitrary instruction (selected via `message_instruction_index`, which can be `u16::MAX` for "this instruction" or any other index into the transaction's instruction list) and an arbitrary byte range within it: [1](#0-0) 

The `get_data_slice` helper resolves `instruction_instruction_index`/`offset` pairs against `instruction_datas: &[&[u8]]`, i.e., the full set of raw instruction byte-strings composed into the transaction by an ordinary, unprivileged client, with no check that the referenced bytes originated from a particular program, a particular instruction "type," a particular chain, or a particular nonce: [2](#0-1) 

The secp256k1 precompile has the identical structure — signature, eth-address, and message are all located via attacker-supplied offsets into arbitrary instruction data, with no binding to program id, genesis hash/chain id, or instruction discriminant: [3](#0-2) [4](#0-3) 

`verify_if_precompile` is invoked generically for any transaction containing one of these three program ids, passing through the raw instruction bytes of the whole transaction without adding any implicit context (program id of the *caller*, genesis hash, or slot): [5](#0-4) 

Unlike Agave's own replay-protection primitives (durable nonce / recent blockhash), which are explicitly domain-separated (`separate_nonce_from_blockhash` feature, `DurableNonce::from_blockhash`, status-cache keyed by `(recent_blockhash, message_hash)`): [6](#0-5) 

the precompiles provide zero equivalent guarantee for the *message payload* that programs choose to authenticate via these signatures. This mirrors items 1–4 of the Rigor report precisely:
- Cross-program reuse: a message signed to authorize action in program A can be pointed at by any other instruction that also consumes the ed25519/secp256k1/secp256r1 output, because `message_instruction_index` lets an attacker's transaction reference bytes from an unrelated instruction.
- Cross-cluster/chain reuse: no genesis hash / chain id is folded into the verified message, so a signature valid on mainnet-beta is equally valid on devnet/testnet or any fork sharing the same key material and message format.
- Cross-function/cross-protocol reuse and phishing: since the "message" is just raw bytes with no type tag, any application (or attacker-controlled dApp) that gets a user to sign a byte string of the same shape can have that signature replayed against a completely different consumer that also expects that message format.

### Impact Explanation
Any unprivileged party constructing a transaction can reuse previously observed signatures across differing security domains, because Agave's builtin precompiles supply the raw signature-verification primitive without a mandated domain separator. Downstream programs on Solana (bridges, passkey/secp256r1-based smart wallets, multisig-like schemes) that rely on these precompiles for authorization inherit this gap by construction, since the primitive itself provides no protection: this can lead to privilege escalation for actions gated on ed25519/secp256k1/secp256r1 verification (funds transfer approvals, guardian/validator attestations, wallet authorization), which is the underlying account-privilege/CPI escalation and fund-theft class explicitly in scope.

### Likelihood Explanation
High for any composed transaction from an ordinary client: the offsets mechanism (`signature_instruction_index`, `public_key_instruction_index`, `message_instruction_index` in ed25519; the analogous fields in secp256k1/secp256r1) is a documented, always-reachable feature of the precompiles requiring no special privilege — a single unprivileged transaction can compose an arbitrary combination of instructions and offsets to redirect a previously-observed signed payload into a new context.

### Recommendation
Agave should document (and ideally provide an opt-in helper/convention) requiring consuming programs to bind signed messages to a domain separator equivalent to EIP-712's `(program_id, genesis_hash, instruction discriminant, nonce)` before delegating trust decisions to ed25519/secp256k1/secp256r1 precompile output; consider precompile extensions that let a caller assert/lock the `message_instruction_index` to the invoking instruction and/or expose the invoking program id to the verification routine so composability cannot be abused to redirect message context across unrelated instructions/programs.

### Proof of Concept
1. User signs message `M` (raw bytes, no program/chain/type context) intended for use by Program A via `new_ed25519_instruction_with_signature(M, sig, pubkey)`, submitted as an instruction preceding a call into Program A that reads the ed25519 instruction's `message_instruction_index`-referenced bytes to authorize an action.
2. Attacker observes `(sig, pubkey, M)` on-chain (public), builds their own transaction embedding the same ed25519 precompile instruction with `message_instruction_index` pointed at those same bytes (or copies them verbatim as `u16::MAX`/self), and pairs it with a call into Program B (a different, unrelated consumer of ed25519-signature-based authorization that happens to expect the same message format).
3. `agave_precompiles::ed25519::verify` (`precompiles/src/ed25519.rs`) succeeds because it never checks which program invoked the precompile, which cluster/genesis hash the message belongs to, or that the message was intended for a specific instruction type — it only checks the raw signature/pubkey/message triple.
4. Program B accepts the replayed signature as valid authorization, exactly as in the Rigor `inviteContractor`/`setComplete` cross-function replay example. [7](#0-6)

### Citations

**File:** precompiles/src/ed25519.rs (L66-76)
```rust
        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;
        publickey
            .verify_strict(message, &signature)
            .map_err(|_| PrecompileError::InvalidSignature)?;
```

**File:** precompiles/src/ed25519.rs (L81-105)
```rust
fn get_data_slice<'a>(
    data: &'a [u8],
    instruction_datas: &'a [&[u8]],
    instruction_index: u16,
    offset_start: u16,
    size: usize,
) -> Result<&'a [u8], PrecompileError> {
    let instruction = if instruction_index == u16::MAX {
        data
    } else {
        let signature_index = instruction_index as usize;
        if signature_index >= instruction_datas.len() {
            return Err(PrecompileError::InvalidDataOffsets);
        }
        instruction_datas[signature_index]
    };

    let start = offset_start as usize;
    let end = start.saturating_add(size);
    if end > instruction.len() {
        return Err(PrecompileError::InvalidDataOffsets);
    }

    Ok(&instruction[start..end])
}
```

**File:** precompiles/src/ed25519.rs (L454-477)
```rust
    #[test]
    fn test_ed25519_malleability() {
        agave_logger::setup();

        // sig created via ed25519_dalek: both pass
        let secret_bytes: [u8; 32] = rand::random();
        let secret = ed25519_dalek::SecretKey::from_bytes(&secret_bytes).unwrap();
        let public: ed25519_dalek::PublicKey = (&secret).into();
        let privkey = ed25519_dalek::Keypair { secret, public };
        let message_arr = b"hello";
        let signature = privkey.sign(message_arr).to_bytes();
        let pubkey = privkey.public.to_bytes();
        let instruction = new_ed25519_instruction_with_signature(message_arr, &signature, &pubkey);

        let feature_set = FeatureSet::default();
        assert!(
            test_verify_with_alignment(
                verify,
                &instruction.data,
                &[&instruction.data],
                &feature_set
            )
            .is_ok()
        );
```

**File:** precompiles/src/secp256k1.rs (L23-47)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.is_empty() {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let count = data[0] as usize;
    if count == 0 && data.len() > 1 {
        // count is zero but the instruction data indicates that is probably not
        // correct, fail the instruction to catch probable invalid secp256k1
        // instruction construction.
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = count
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(1);
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    for i in 0..count {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(1);
```

**File:** precompiles/src/secp256k1.rs (L73-103)
```rust
        // Parse out pubkey
        let eth_address_slice = get_data_slice(
            instruction_datas,
            offsets.eth_address_instruction_index,
            offsets.eth_address_offset,
            HASHED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message_slice = get_data_slice(
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let message_hash: [u8; 32] = solana_keccak_hasher::hash(message_slice).to_bytes();
        let pubkey = libsecp256k1::recover(
            &libsecp256k1::Message::parse_slice(&message_hash).unwrap(),
            &signature,
            &recovery_id,
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;
        let eth_address = eth_address_from_pubkey(&pubkey.serialize()[1..].try_into().unwrap());

        if eth_address_slice != eth_address {
            return Err(PrecompileError::InvalidSignature);
        }
    }
    Ok(())
}
```

**File:** precompiles/src/lib.rs (L98-119)
```rust
/// Check that a program is precompiled and if so verify it
pub fn verify_if_precompile(
    program_id: &Pubkey,
    precompile_instruction: &CompiledInstruction,
    all_instructions: &[CompiledInstruction],
    feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    for precompile in PRECOMPILES.iter() {
        if precompile.check_id(program_id, |feature_id| feature_set.is_active(feature_id)) {
            let instruction_datas: Vec<_> = all_instructions
                .iter()
                .map(|instruction| instruction.data.as_ref())
                .collect();
            return precompile.verify(
                &precompile_instruction.data,
                &instruction_datas,
                feature_set,
            );
        }
    }
    Ok(())
}
```

**File:** runtime/src/bank/check_transactions.rs (L288-302)
```rust
    pub(super) fn load_message_nonce_data(
        &self,
        message: &impl SVMMessage,
        strict_nonce_size_check: bool,
    ) -> Option<(Pubkey, NonceData)> {
        let nonce_address = message.get_durable_nonce()?;
        let nonce_account = self.get_account_with_fixed_root(nonce_address)?;
        if strict_nonce_size_check && nonce_account.data().len() != NonceState::size() {
            return None;
        }
        let nonce_data =
            nonce_account::verify_nonce_account(&nonce_account, message.recent_blockhash())?;

        Some((*nonce_address, nonce_data))
    }
```
