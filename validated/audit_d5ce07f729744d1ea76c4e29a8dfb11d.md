### Title
Non-deterministic OS output from `OsRng`-based key generation in state-diff encryption breaks state-transition determinism - ([File: crates/starknet_os/src/hints/hint_implementation/output.rs])

### Summary
The Starknet OS hint `calculate_keys_using_sha256_hash` (Cairo hint `GenerateKeysUsingSha256Hash`) generates the symmetric key and committee private keys used to encrypt the state diff for data availability by seeding with `rand::rngs::OsRng`, a source of true, non-reproducible system randomness, rather than a value deterministically derived from the block/state-diff content. This makes the OS's committed output (the encrypted DA segment, hence the block's output/commitments) non-reproducible across independent executions of the identical block, breaking the fundamental determinism requirement of the state-transition function.

### Finding Description
`calculate_keys_using_sha256_hash` fills 32 random bytes from `OsRng` and mixes them with a hash of the compressed state diff to derive `random_key_seed`, from which `symmetric_key` and `sn_private_keys` are derived: [1](#0-0) 

These keys are then used inside the Cairo hint context `encrypt_state_diff` to compute the public keys, encrypted symmetric keys, and the ciphertext of the state diff, all of which are written into the OS `output_pointer` (i.e., become part of the block's committed output / DA data): [2](#0-1) 

This output flows through `process_data_availability` / `serialize_data_availability`, which relocates it directly into the OS `output_ptr` used to build the block's commitments: [3](#0-2) 

Because `OsRng` produces different random bytes on every invocation, two independent executions of the Starknet OS over the exact same block inputs (e.g., by a proving node vs. a validating node performing OS re-execution, or any two honest nodes independently re-deriving the state transition) will compute different `symmetric_key`/`sn_private_keys` values and therefore different ciphertext for the encrypted state diff. The state diff's actual plaintext content is identical in both runs — only the cryptographic keys/output bytes differ — yet this ciphertext is part of the OS's committed output that feeds block hash/state-diff commitments. The additional mixing-in of the compressed state diff bytes (labeled "extra defense against potential attacks on randomness source") does not remove the non-determinism, since the dominant entropy source (`OsRng`) still differs on every call.

### Impact Explanation
Any component of the sequencer/validator/prover pipeline that must reproduce the exact same OS output for a given block (to validate it, sign it, or generate/verify a STARK proof against a previously committed output) will diverge whenever the committee-encryption DA feature is active (`n_keys > 0`). This results in honest-node divergence on the block's output/commitment/block hash for identical logical block content, and can render the network unable to confirm blocks that use this feature (since the sequencer's committed output can never be reproduced by an independent re-executor or by the prover attempting to match the previously-published commitment).

### Likelihood Explanation
This is not attacker-dependent: it triggers deterministically on every block where the state-diff committee-encryption feature is enabled (`n_keys > 0`), which is activated simply by any transaction from an ordinary sender producing a state diff under that configuration. No malicious input is required — the bug is inherent in the hint's use of `OsRng` for key material that must be reproducible across independent honest executions of the state-transition function.

### Recommendation
Replace `OsRng`-derived randomness with a value deterministically derived purely from block-scoped data available to all executors (e.g., a Poseidon/SHA256 hash over the block hash, the full compressed state diff, and a fixed domain separator), removing any dependency on a non-reproducible entropy source, so that every independent execution of the OS over identical inputs produces bit-for-bit identical `symmetric_key`/`sn_private_keys` and therefore identical committed output.

### Proof of Concept
1. Run the Starknet OS Cairo program twice (e.g., via the `starknet_os_flow_tests` harness with `use_kzg_da`/`private_keys` set as in `test_encrypted_state_diff`) over the exact same block inputs and state diff. [4](#0-3) 
2. Observe that `calculate_keys_using_sha256_hash` calls `OsRng.fill_bytes` on each run, producing different `random_bytes` each time: [5](#0-4) 
3. Compare the resulting `SymmetricKey`, `SnPrivateKeys`, and the final encrypted DA output segment between the two runs — they differ despite identical state diff/plaintext, demonstrating that the OS's committed output for the same block is non-reproducible.

### Citations

**File:** crates/starknet_os/src/hints/hint_implementation/output.rs (L149-166)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L224-270)
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

func serialize_data_availability{output_ptr: felt*}(da_start: felt*, da_end: felt*) {
    // Relocate data availability segment to the correct place in the output segment.
    relocate_segment(src_ptr=da_start, dest_ptr=output_ptr);
    let output_ptr = da_end;

    %{ SetProofFactTopology %}

    return ();
}
```

**File:** crates/starknet_os_flow_tests/src/tests.rs (L370-419)
```rust
#[rstest]
#[tokio::test]
async fn test_encrypted_state_diff(
    #[values(false, true)] use_kzg_da: bool,
    #[values(false, true)] full_output: bool,
    #[values(None, Some(vec![]), Some(vec![Felt::THREE, Felt::ONE]))] private_keys: Option<
        Vec<Felt>,
    >,
) {
    let (mut test_builder, [test_contract_address]) = TestBuilder::create_standard_with_config(
        [(
            FeatureContract::TestContract(CairoVersion::Cairo1(RunnableCairo1::Casm)),
            nontrivial_test_contract_constructor_calldata(),
        )],
        TestBuilderConfig { use_kzg_da, full_output, private_keys: private_keys.clone() },
    )
    .await;

    // Invoke a function on the test contract that changes the storage.
    let (key, value) = (Felt::from(10u8), Felt::from(11u8));
    let calldata = create_calldata(test_contract_address, "test_storage_read_write", &[key, value]);
    test_builder.add_funded_account_invoke(invoke_tx_args! { calldata });

    // Run the test and assert the diff is as expected.
    let test_output = test_builder.build_and_run().await;
    let perform_global_validations = true;
    let partial_state_diff = StateDiff {
        storage_updates: HashMap::from([(
            test_contract_address,
            HashMap::from([(
                StarknetStorageKey(key.try_into().unwrap()),
                StarknetStorageValue(value),
            )]),
        )]),
        ..Default::default()
    };
    test_output.perform_validations(perform_global_validations, Some(&partial_state_diff));

    let scenario_name = format!(
        "test_encrypted_state_diff_use_kzg_da_{}_full_output_{}_private_keys_{}",
        use_kzg_da,
        full_output,
        match private_keys {
            None => "none".to_string(),
            Some(keys) if keys.is_empty() => "empty".to_string(),
            Some(keys) => keys.iter().map(|key| key.to_string()).collect::<Vec<_>>().join("_"),
        }
    );
    test_output.expect_hint_coverage(&scenario_name);
}
```
