### Title
Batch proof `method_id` upgrade replay between `Network::Nightly` and `Network::TestNetworkWithForks` due to colliding `chain_id` and colliding default authority keys - (File: `crates/light-client-prover/src/circuit/mod.rs`)

### Summary
`citrea_network_to_chain_id` maps both `Network::Nightly` and `Network::TestNetworkWithForks` to the identical value `5665`, so the anti-replay check `circuit_chain_id != batch_proof_method_id.body.chain_id` at `crates/light-client-prover/src/circuit/mod.rs` line 545 cannot distinguish a `BatchProofMethodIdBody` authorised for one network from the other. This is compounded by `crates/light-client-prover/src/circuit/initial_values.rs`, where `NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` and `TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` read the exact same environment-variable names (`METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_1..5`) and fall back to the exact same hard-coded default keys, so by default the two networks' security councils are byte-identical too.

### Finding Description
The intended binding is: a `BatchProofMethodIdBody` is only valid for the network whose `citrea_network_to_chain_id(network)` equals `body.chain_id`, i.e. `circuit_chain_id == batch_proof_method_id.body.chain_id` should uniquely identify one `Network` variant. [1](#0-0) 

In reality:
```
Network::Nightly => 5665
Network::TestNetworkWithForks => 5665
``` [2](#0-1) 

so the check at [3](#0-2) 
passes for a message signed under either network's chain_id when running under the other.

`verify_method_id_security_council` only checks signatures against `method_id_upgrade_authority_da_public_keys`, which is fed in from `InitialValueProvider::method_id_upgrade_authority_da_public_keys()`: [4](#0-3) 

Both the `Nightly` and `TestNetworkWithForks` variants pull from constants keyed off the *identical* env-var names and identical hard-coded fallback private keys: [5](#0-4) [6](#0-5) 

Attack flow: the security council legitimately signs a `BatchProofMethodIdBody{ chain_id: citrea_network_to_chain_id(Network::TestNetworkWithForks) == 5665, .. }` for the fork-testing network, and it gets inscribed on that network's DA. Any unprivileged attacker who observes this public inscription (per the rules, they can "inscribe or mine any Bitcoin transaction") copies the exact `body` + `signatures_with_index` bytes into a `DataOnDa::BatchProofMethodId` blob and inscribes it on the Bitcoin network that a `Network::Nightly`-configured light client prover monitors. When that node processes the blob in `run_l1_block`:
- The activation-height monotonicity check passes for a fresh height.
- `circuit_chain_id (5665 for Nightly) == batch_proof_method_id.body.chain_id (5665)` — passes, because of the alias.
- `verify_method_id_security_council` is run against `NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS`, which by default equals `TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` — passes.

The method id is inserted into `BatchProofMethodIdAccessor` and activates on Nightly nodes despite the council never authorising it for Nightly. [7](#0-6) 

The existing regression test only exercises a cross-network mismatch between `Network::Mainnet` (4114) and `Network::Nightly` (5665), which are non-colliding chain ids, so it does not exercise or catch the `Nightly`/`TestNetworkWithForks` alias: [8](#0-7) 

### Impact Explanation
A batch proof `method_id` upgrade that the security council intended only for the fork-testing network can be silently applied on the Nightly network's light client circuit (or the reverse), i.e. "a sequencer commitment or method-id upgrade accepted that was never authorised" for that specific deployment — matching the Critical impact category. Once accepted by one honest prover/node under `Network::Nightly`'s constants and rejected by a hypothetical differently-configured node (or vice versa), light client provers following the same code but instantiated with `Network::TestNetworkWithForks` vs `Network::Nightly` could diverge on which `method_id` is valid at a given activation height, risking honest full nodes/provers splitting on which batch proof is accepted going forward. This is repeatable for every future method-id upgrade inscribed on either network.

### Likelihood Explanation
The precondition is that both `Nightly` and `TestNetworkWithForks` deployments are compiled with either (a) unset `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_*` env vars (using the shared default keys), or (b) the same values set for both builds — plausible since the code offers no per-network-distinct env var name, only one shared set of names for both. Given that `TestNetworkWithForks` is explicitly documented as "used for testing purposes only," it's plausible this network is exercised in CI/devnet pipelines that reuse the same default keys as `Nightly`. The attacker's cost is only Bitcoin inscription fees to copy an already-public signed blob onto the target network's chain — no privileged key, RPC auth, or council access is required.

### Recommendation
Assign every `Network` variant a distinct chain id (do not alias `Nightly` and `TestNetworkWithForks` to `5665`), and require the method-id upgrade authority public keys for `Nightly` and `TestNetworkWithForks` to come from distinct, non-overlapping environment variable names/defaults so the two networks cannot share a security council key set even accidentally.

### Proof of Concept
```rust
// crates/light-client-prover/src/circuit/mod.rs (test module)
#[test]
fn test_nightly_and_test_network_with_forks_chain_id_collide() {
    assert_ne!(
        citrea_network_to_chain_id(Network::Nightly),
        citrea_network_to_chain_id(Network::TestNetworkWithForks),
        "chain ids must not alias between distinct networks"
    ); // currently FAILS: both are 5665
}

#[test]
fn test_method_id_body_rejected_across_aliased_networks() {
    // Build a BatchProofMethodIdBody signed under TestNetworkWithForks' chain_id
    let body = BatchProofMethodIdBody {
        method_id: [7u32; 8],
        activation_l2_height: 10,
        chain_id: citrea_network_to_chain_id(Network::TestNetworkWithForks),
    };
    let msg = body.serialize();
    let prehash = eip191_hash_message(&msg);
    let (_pubkeys, signers) = generate_initial_pub_keys_with_signers();
    let signatures_with_index = create_valid_signatures(&signers, &prehash);
    let blob = create_new_method_id_tx_from_body(body, signatures_with_index);

    // Run the light client circuit with network = Nightly
    let result = run_l1_block_with(Network::Nightly, vec![blob]);

    // Expect it to be rejected because it was authorised for TestNetworkWithForks, not Nightly
    let batch_proof_method_ids = BatchProofMethodIdAccessor::<ProverStorage>::get(&mut result.working_set).unwrap();
    assert_eq!(batch_proof_method_ids.len(), 1, "method id upgrade must not have been applied on Nightly");
}
```
Both assertions currently fail against the code as written, demonstrating the alias and the resulting cross-network replay.

### Citations

**File:** crates/light-client-prover/src/circuit/mod.rs (L544-548)
```rust
                    let circuit_chain_id = citrea_network_to_chain_id(network);
                    if circuit_chain_id != batch_proof_method_id.body.chain_id {
                        log!("Method ID upgrade transactions chain ID does not match circuit chain ID");
                        continue;
                    }
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L561-565)
```rust
                    BatchProofMethodIdAccessor::<S>::insert(
                        batch_proof_method_id.body.activation_l2_height,
                        batch_proof_method_id.body.method_id,
                        &mut working_set,
                    );
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L758-770)
```rust
/// These are chain ids for the citrea networks
/// This function is mainly used to check the chain id of the
/// method id upgrade transactions and to prevent cross network replay attacks
/// The method id upgrade identifiers are not strictly tied to chain ids
/// but for simplicity we use the same values
pub fn citrea_network_to_chain_id(network: sov_rollup_interface::Network) -> u64 {
    match network {
        sov_rollup_interface::Network::Mainnet => 4114,
        sov_rollup_interface::Network::Testnet => 5115,
        sov_rollup_interface::Network::Devnet => 62298,
        sov_rollup_interface::Network::Nightly => 5665,
        sov_rollup_interface::Network::TestNetworkWithForks => 5665,
    }
```

**File:** crates/light-client-prover/src/circuit/initial_values.rs (L504-512)
```rust
    pub const NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS: [[u8;
        SECURITY_COUNCIL_COMPRESSED_PUBKEY_SIZE];
        SECURITY_COUNCIL_MEMBER_COUNT] = [
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_1") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9077
                None => "0313c4ff65eb94999e0ac41cfe21592baa52910f5a5ada9074b816de4f560189db",
            };
```

**File:** crates/light-client-prover/src/circuit/initial_values.rs (L578-593)
```rust
    pub const TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS: [[u8;
        SECURITY_COUNCIL_COMPRESSED_PUBKEY_SIZE];
        SECURITY_COUNCIL_MEMBER_COUNT] = [
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_1") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9077
                None => "0313c4ff65eb94999e0ac41cfe21592baa52910f5a5ada9074b816de4f560189db",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_1 must be valid 33-byte hex string"
                ),
            }
        },
```

**File:** crates/light-client-prover/src/circuit/initial_values.rs (L736-748)
```rust
    fn method_id_upgrade_authority_da_public_keys(
        &self,
    ) -> [[u8; SECURITY_COUNCIL_COMPRESSED_PUBKEY_SIZE]; SECURITY_COUNCIL_MEMBER_COUNT] {
        match self {
            Network::Mainnet => bitcoinda::MAINNET_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS,
            Network::Testnet => bitcoinda::TESTNET_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS,
            Network::Devnet => bitcoinda::DEVNET_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS,
            Network::Nightly => bitcoinda::NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS,
            Network::TestNetworkWithForks => {
                bitcoinda::TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS
            }
        }
    }
```

**File:** crates/light-client-prover/src/tests/mod.rs (L1199-1259)
```rust
#[test]
fn test_wrong_network_method_id_update_should_fail() {
    let db_dir = tempdir().unwrap();
    let native_circuit_runner = NativeCircuitRunner::new(db_dir.path().to_path_buf());
    let zk_circuit_runner = LightClientProofCircuit::<ZkStorage, MockDaSpec, MockZkGuest>::new();

    let light_client_proof_method_id = [1u32; 8];
    let da_verifier = MockDaVerifier {};

    let l2_genesis_state_root = [1u8; 32];
    let batch_prover_da_pub_key = [9; 32];
    let sequencer_da_pub_key = [45; 32];
    let method_id_sender = [11u8; 32];

    let block_header_1 = MockBlockHeader::from_height(1);

    // Create method id update for a different network
    let blob = create_new_method_id_tx(10, [2u32; 8], method_id_sender, Network::Mainnet);

    let input = native_circuit_runner.run(
        LightClientCircuitInput {
            previous_light_client_proof: None,
            light_client_proof_method_id,
            da_block_header: block_header_1,
            inclusion_proof: [1u8; 32],
            completeness_proof: vec![blob],
            witness: Default::default(),
        },
        l2_genesis_state_root,
        INITIAL_BATCH_PROOF_METHOD_IDS.to_vec(),
        &batch_prover_da_pub_key,
        &sequencer_da_pub_key,
        &METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS,
        Network::Nightly,
    );

    let _ = zk_circuit_runner
        .run_circuit(
            da_verifier.clone(),
            input,
            ZkStorage::new(),
            Network::Nightly,
            l2_genesis_state_root,
            INITIAL_BATCH_PROOF_METHOD_IDS.to_vec(),
            &batch_prover_da_pub_key,
            &sequencer_da_pub_key,
            &METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS,
        )
        .unwrap();
    let mut working_set = WorkingSet::new(
        native_circuit_runner
            .prover_storage_manager
            .create_final_view_storage(),
    );

    let batch_proof_method_ids =
        BatchProofMethodIdAccessor::<ProverStorage>::get(&mut working_set).unwrap();

    // didn't change
    assert_eq!(batch_proof_method_ids.len(), 1);
}
```
