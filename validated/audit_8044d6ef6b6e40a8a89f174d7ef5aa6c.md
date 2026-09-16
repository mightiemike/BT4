### Title
Non-deterministic (true-random) key generation inside the Starknet OS breaks re-execution determinism, causing honest-node divergence in DA/output for state-diff-encrypted blocks - ([File: crates/starknet_os/src/hints/hint_implementation/output.rs])

### Summary
The Starknet OS hint `GenerateKeysUsingSha256Hash` (implemented by `calculate_keys_using_sha256_hash`) generates the committee-encryption symmetric key and per-committee `sn_private_keys` using `OsRng.fill_bytes`, a genuinely non-deterministic entropy source, instead of a deterministic, block-derived seed. This mirrors the PyCrypto CVE-2012-2417 bug class ("insufficiently/incorrectly derived random values used for key generation reachable by protocol participants"), except here the failure mode is not weak entropy but *unreproducible* entropy inside a component (the Starknet OS) whose entire correctness model depends on every honest node computing byte-identical outputs.

### Finding Description
`calculate_keys_using_sha256_hash` seeds key derivation with true randomness: [1](#0-0) 

This hint backs the Cairo `encrypt_state_diff` function, which is invoked unconditionally during `process_data_availability` whenever committee public keys are configured (`n_keys != 0`), i.e., on every ordinary block containing state changes once the DA-encryption feature is enabled — no malicious input or privileged role is required, just normal transaction processing that produces a non-empty state diff: [2](#0-1) [3](#0-2) 

Crucially, the codebase already recognizes that hint-level randomness inside the OS must be deterministic/seeded, precisely because the OS is re-executed independently by multiple parties (e.g., the sequencer producing the trace vs. any full node/verifier reproducing the OS run) and must reach the exact same output. This is explicit in the override of the `RandomEcPoint` core hint: [4](#0-3) 

`calculate_keys_using_sha256_hash` violates this invariant: it mixes `OsRng` bytes (true entropy, different every run) into the seed for `symmetric_key` and each `sn_private_key`: [5](#0-4) 

The derived private keys feed into `output_sn_public_keys` (elliptic-curve public keys written to the OS output) and `output_encrypted_symmetric_key` (ECDH-masked symmetric key), and the symmetric key is used to XOR/mask the compressed state diff itself via `encrypt`. All of these values — public keys, encrypted symmetric key, and ciphertext — end up in the OS's output/DA segment (or KZG blob) that different honest parties must reproduce identically.

### Impact Explanation
Because the keys are derived from a non-reproducible entropy source, two honest re-executions of the Starknet OS over the exact same block input (same transactions, same state, same compressed state diff) will produce different `symmetric_key`/`sn_private_keys`, hence different `sn_public_keys`, different `encrypted_symmetric_key` values, and a different `encrypted_state_diff` ciphertext. Any component (full node re-executing the OS to validate the produced DA/output, or any independent proof/verification pipeline) that recomputes the OS run and compares its output/DA segment against the sequencer's committed one will diverge — a classic honest-node divergence bug for a component the rules explicitly list as in scope ("Starknet OS re-execution"). Since this data becomes part of the committed block output segment / KZG blob (i.e., part of what must be agreed upon network-wide), this can prevent nodes from confirming/validating new blocks whenever the committee-DA-encryption feature is active, satisfying the "honest-node divergence" / "network unable to confirm new transactions" acceptance bar.

### Likelihood Explanation
No attacker action is required beyond normal usage: any block with a non-empty state diff, on a deployment where committee public keys (`n_public_keys > 0`) are configured for DA encryption, triggers this hint every time the OS is executed. Every independent re-execution of the same block by a different party is guaranteed to diverge because `OsRng` is reseeded per invocation.

### Recommendation
Replace the `OsRng`-derived entropy in `calculate_keys_using_sha256_hash` with a deterministic seed derived solely from block-committed data (e.g., the compressed state diff, block number/hash, and any other fields already available to every party performing the re-execution) — analogous to how `RandomEcPoint` was made deterministic via seeded randomness in `common_hint_processor.rs`. Remove or condition out the `OsRng.fill_bytes` call so that all honest re-executions of the OS for a given block produce bit-identical `symmetric_key`/`sn_private_keys`/derived outputs.

### Proof of Concept
1. Configure a chain with committee public keys set (`n_public_keys > 0`) so `process_data_availability`/`encrypt_state_diff` executes.
2. Run the Starknet OS twice over the identical block input (same transactions/state) — e.g., once as the "sequencer" run and once as an independent "verifier" re-execution, both invoking `calculate_keys_using_sha256_hash`.
3. Observe that `symmetric_key`, `sn_private_keys`, `sn_public_keys`, `encrypted_symmetric_key`, and `encrypted_state_diff` differ between the two runs (since each call reseeds from `OsRng`), even though all other inputs are identical — demonstrating that the OS output/DA segment for this block is not reproducible, i.e., honest-node divergence for any block where the DA-encryption feature is enabled.

### Citations

**File:** crates/starknet_os/src/hints/hint_implementation/output.rs (L149-203)
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

    // Private keys: derive with a counter and truncate to 31 bytes (248 bits)
    // to ensure result is < 2^248 < EC group order < PRIME. This is required for
    // the Diffie-Hellman elliptic curve.

    let mut priv_counter: u8 = 0;
    let mut next_private_key = || -> MaybeRelocatable {
        let hash = Sha256::new()
            .chain_update(&random_key_seed)
            .chain_update(PRIV_LABEL)
            .chain_update([priv_counter])
            .finalize();
        priv_counter += 1;

        // Use only first 31 bytes to ensure < 2^248.
        let mut key_bytes = [0u8; 32];
        key_bytes[1..].copy_from_slice(&hash[..31]);
        MaybeRelocatable::from(Felt::from_bytes_be(&key_bytes))
    };

    let n_keys = ctx.get_integer(Ids::NKeys)?;
    let num_private_keys = felt_to_usize(&n_keys)?;
    let private_keys: Vec<MaybeRelocatable> =
        (0..num_private_keys).map(|_| next_private_key()).collect();
    let private_keys_start = ctx.vm.gen_arg(&private_keys)?;

    ctx.insert_value(Ids::SnPrivateKeys, private_keys_start)?;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L224-260)
```text
// Returns the final data-availability to output.
func process_data_availability{range_check_ptr, ec_op_ptr: EcOpBuiltin*}(
    state_updates_start: felt*,
    state_updates_end: felt*,
    compress_state_updates: felt,
    n_keys: felt,
    public_keys: felt*,
) -> (da_start: felt*, da_end: felt*) {
    if (compress_state_updates == 0) {
        return (da_start=state_updates_start, da_end=state_updates_end);
    }

    alloc_locals;

    // Compress the state updates.
    local compressed_start: felt*;
    %{ SetCompressedStart %}
    let compressed_dst = compressed_start;
    with compressed_dst {
        compress(data_start=state_updates_start, data_end=state_updates_end);
    }

    if (n_keys == 0) {
        // No public keys - skip the state updates encryption.
        return (da_start=compressed_start, da_end=compressed_dst);
    }

    // Encrypt the compressed state updates.
    let (encrypted_start, encrypted_end) = encrypt_state_diff(
        compressed_start=compressed_start,
        compressed_end=compressed_dst,
        n_keys=n_keys,
        public_keys=public_keys,
    );

    return (da_start=encrypted_start, da_end=encrypted_end);
}
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

**File:** crates/starknet_os/src/hint_processor/common_hint_processor.rs (L101-127)
```rust
            // TODO(Dori): Consider moving cairo1 hint handling and the [get_rng] method to the
            //   [SnosHintProcessor], as the aggregator should not use randomness.
            // Cairo1 syscall or Cairo1 core hint.
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
                    cairo_lang_runner::insert_value_to_cellref!(vm, x, random_x)?;
                    cairo_lang_runner::insert_value_to_cellref!(vm, y, random_y)?;
                    Ok(HintExtension::default())
```
