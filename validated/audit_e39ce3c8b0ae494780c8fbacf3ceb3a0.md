## Title
Starknet OS committee-DA encryption hint uses non-deterministic `OsRng` instead of the block-derived deterministic RNG, breaking bit-for-bit re-execution determinism of the committed program output - (File: crates/starknet_os/src/hints/hint_implementation/output.rs)

### Summary
The `calculate_keys_using_sha256_hash` hint, invoked by the Cairo `GenerateKeysUsingSha256Hash` hint inside `encrypt_state_diff` (used when the encrypted-DA/committee feature is enabled), seeds its symmetric key and per-committee-member StarkNet private keys from the OS-process's `OsRng`, an operating-system source of non-deterministic randomness, rather than from the block-derived, deterministic `StdRng` that the rest of the Starknet OS hint processor deliberately uses for all in-VM randomness.

### Finding Description
`SnosHintProcessor` is explicitly designed so that any randomness consumed while running the Starknet OS program is deterministic and reproducible from the block being processed: its `rng` field is a `rand::rngs::StdRng` seeded via `Poseidon::hash_array` over the block hashes and a configured salt [1](#0-0) , and this seeded RNG is threaded through `get_rng()` and used, for instance, to make `RandomEcPoint` deterministic across re-executions [2](#0-1) .

However, `calculate_keys_using_sha256_hash` bypasses this deterministic RNG entirely and pulls 32 bytes directly from `rand::rngs::OsRng`: [3](#0-2) 

This hint backs the `GenerateKeysUsingSha256Hash` Cairo hint inside `encrypt_state_diff`, which generates the symmetric key and per-committee-member StarkNet private keys used to encrypt the state diff for committee members: [4](#0-3) . That function is called from `serialize_os_output`, which is part of the canonical OS-output serialization path (the program output that is committed to and later verified/proved): [5](#0-4) .

Because `OsRng` draws from the OS entropy source rather than from the block-derived seed, two independent (honest) executions of the Starknet OS over the *same* block input (e.g. sequencer proving vs. an independent re-execution/verification run, or a retry after a crash) will derive different `symmetric_key` and `sn_private_keys` values, and therefore different `sn_public_keys` and different `encrypted_symmetric_key`/ciphertext bytes written into the OS program output.

### Impact Explanation
The encrypted state-diff segment produced by `encrypt_state_diff` is embedded directly in the OS program output produced by `serialize_os_output`/`process_os_output`, which is the artifact that gets proven and whose hash (the "fact") is what the L1 verifier and independent re-executors check for agreement. Because the symmetric/private key material is generated with a non-deterministic system RNG instead of the protocol's deterministic, block-seeded RNG, two honest nodes/provers executing the identical Starknet OS program on the identical block input will produce differing program outputs. This is a direct "honest-node divergence" in the Starknet OS re-execution path: any two independently-computed proofs/outputs for the same block will not match, since the encrypted committee output differs, breaking fact/commitment agreement and the ability to reliably reconstruct or verify the committed output deterministically.

### Likelihood Explanation
This code path executes whenever the committee/encrypted-DA output feature is exercised (i.e., `public_keys`/committee configuration is set and `use_kzg_da` triggers the encrypted-output branch) as part of normal, protocol-mandated OS execution — it is not conditioned on any attacker input beyond the block simply being processed with this feature enabled. Given the codebase's own explicit design pattern of using the seeded `StdRng` everywhere else specifically to guarantee determinism (as documented in the `RandomEcPoint` override), the divergence triggers deterministically every time this hint runs with the committee-DA feature active, with no additional preconditions.

### Recommendation
Replace the direct `OsRng.fill_bytes` call in `calculate_keys_using_sha256_hash` with the hint processor's block-seeded deterministic RNG (`CommonHintProcessor::get_rng()`), consistent with how all other Starknet OS randomness is generated, so that the derived symmetric key and private keys are fully reproducible from the block input.

### Proof of Concept
1. Configure a block whose OS execution uses the committee/encrypted-DA output path (`public_keys` set, encryption enabled).
2. Run the Starknet OS twice over the identical `OsBlockInput`/`OsHintsConfig` (same block hashes, same salt) on two different processes/machines (simulating two honest provers/re-executors).
3. Because `calculate_keys_using_sha256_hash` draws its seed from `OsRng` (process/host entropy) instead of the deterministic `rng_seed` derived from `new_block_hash`/`rng_seed_salt`, the `symmetric_key`, `sn_private_keys`, `sn_public_keys`, and `encrypted_symmetric_key`/ciphertext values differ between the two runs. [3](#0-2) 
4. The resulting OS program outputs (and thus their fact hashes) differ for the identical block, demonstrating non-deterministic, non-reproducible committed output.

### Citations

**File:** crates/starknet_os/src/hint_processor/snos_hint_processor.rs (L192-202)
```rust
    /// Hashes the block hashes of the given block inputs to get a seed for the random number
    /// generator.
    fn rng_seed(os_block_inputs: &[&'a OsBlockInput], rng_seed_salt: &Option<Felt>) -> Felt {
        Poseidon::hash_array(
            &os_block_inputs
                .iter()
                .map(|block_input| block_input.new_block_hash.0)
                .chain([rng_seed_salt.unwrap_or_default()])
                .collect::<Vec<_>>(),
        )
    }
```

**File:** crates/starknet_os/src/hint_processor/common_hint_processor.rs (L104-124)
```rust
            match hint_data.downcast_ref::<Cairo1Hint>().ok_or(VmHintError::WrongHintData)? {
                // Override the [CoreHint::RandomEcPoint] implementation to make the output
                // deterministic (using seeded randomness).
                Cairo1Hint::Core(CoreHintBase::Core(CoreHint::RandomEcPoint { x, y })) => {
                    // TODO(Dori): use the random_ec_point function from the compiler repo when
                    //   available, instead of inlining the implementation.
                    /// The Beta value of the Starkware elliptic curve.
                    pub const BETA: Felt = Felt::from_hex_unchecked(
                        "0x6f21413efbe40de150e596d72f7a8c5609ad26c15c915c1f4cdfcb99cee9e89",
                    );
                    // Use the seeded randomness.
                    let rng = self.get_rng();
                    let (random_x, random_y) = loop {
                        // Randomizing 31 bytes to make sure it is in range.
                        let x_bytes: [u8; 31] = rng.gen();
                        let random_x = Felt::from_bytes_be_slice(&x_bytes);
                        let random_y_squared = random_x * random_x * random_x + random_x + BETA;
                        if let Some(random_y) = random_y_squared.sqrt() {
                            break (random_x, random_y);
                        }
                    };
```

**File:** crates/starknet_os/src/hints/hint_implementation/output.rs (L149-176)
```rust
pub(crate) fn calculate_keys_using_sha256_hash(mut ctx: HintContext<'_>) -> OsHintResult {
    // Generate a cryptographically secure random seed.
    let mut random_bytes = [0u8; 32];
    OsRng.fill_bytes(&mut random_bytes);

    let mut hasher = Sha256::new();
    hasher.update(random_bytes);

    // In addition, hash the compressed state diff for extra defense against potential attacks on
    // randomness source.
    let compressed_start = ctx.get_ptr(Ids::CompressedStart)?;
    let compressed_end = ctx.get_ptr(Ids::CompressedEnd)?;
    let array_size = (compressed_end - compressed_start)?;
    for i in 0..array_size {
        let felt = ctx.vm.get_integer((compressed_start + i)?)?;
        hasher.update(felt.to_bytes_be());
    }
    let random_key_seed = hasher.finalize().to_vec();

    const SYM_LABEL: &[u8] = b"SYM"; // domain separation: symmetric key
    const PRIV_LABEL: &[u8] = b"PRIV"; // domain separation: private keys

    // Derive the symmetric key (full 32 bytes):
    let symmetric_key = {
        let hash = Sha256::new().chain_update(&random_key_seed).chain_update(SYM_LABEL).finalize();
        Felt::from_bytes_be(&hash.into())
    };
    ctx.insert_value(Ids::SymmetricKey, symmetric_key)?;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/encrypt.cairo (L45-74)
```text
func encrypt_state_diff{range_check_ptr, ec_op_ptr: EcOpBuiltin*}(
    compressed_start: felt*, compressed_end: felt*, n_keys: felt, public_keys: felt*
) -> (encrypted_start: felt*, encrypted_end: felt*) {
    alloc_locals;

    // Generate random symmetric key and random starknet private keys.
    local symmetric_key: felt;
    local sn_private_keys: felt*;
    %{ GenerateKeysUsingSha256Hash %}

    local encrypted_start: felt*;
    %{ SetEncryptedStart %}

    let output_pointer = encrypted_start;
    assert output_pointer[0] = n_keys;
    let output_pointer = &output_pointer[1];

    with output_pointer {
        output_sn_public_keys(n_keys=n_keys, sn_private_keys=sn_private_keys);
        output_encrypted_symmetric_key(
            n_keys=n_keys,
            public_keys=public_keys,
            sn_private_keys=sn_private_keys,
            symmetric_key=symmetric_key,
        );
        encrypt(data_start=compressed_start, data_end=compressed_end, symmetric_key=symmetric_key);
    }

    return (encrypted_start=encrypted_start, encrypted_end=output_pointer);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L78-90)
```text
func serialize_os_output{
    output_ptr: felt*, range_check_ptr, ec_op_ptr: EcOpBuiltin*, poseidon_ptr: PoseidonBuiltin*
}(os_output: OsOutput*, replace_keys_with_aliases: felt, n_public_keys: felt, public_keys: felt*) {
    alloc_locals;

    local use_kzg_da = os_output.header.use_kzg_da;
    local full_output = os_output.header.full_output;
    let compress_state_updates = 1 - full_output;

    // Compute the data availability segment.
    local state_updates_start: felt*;
    let state_updates_ptr = state_updates_start;
    %{ SetStateUpdatesStart %}
```
