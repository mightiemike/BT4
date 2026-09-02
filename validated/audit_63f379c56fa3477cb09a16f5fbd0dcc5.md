### Title
Nightly and TestNetworkWithForks share identical chain_id (5665) and default security-council public keys, allowing cross-network replay of BatchProofMethodId upgrades in `run_l1_block` - (File: crates/light-client-prover/src/circuit/mod.rs)

### Summary
`citrea_network_to_chain_id` maps `Network::Nightly` and `Network::TestNetworkWithForks` to the identical value `5665`, and the default (unset-env-var) security council public keys in `initial_values.rs` are also byte-identical between the two networks. Because `run_l1_block`'s chain-id equality check and `verify_method_id_security_council` operate purely on these two values, a council-signed `BatchProofMethodId` inscription broadcast for Nightly is a fully valid, signature-passing inscription for a TestNetworkWithForks node as well.

### Finding Description
The binding the code claims to enforce is: `circuit_chain_id == body.chain_id` uniquely identifies the network the update was authorized for, i.e. "chain_id X accepted on network N" implies "N is the only network whose upgrade authority could have produced X". This binding is stated explicitly in the doc comment above `citrea_network_to_chain_id`: *"This function is mainly used to check the chain id of the method id upgrade transactions and to prevent cross network replay attacks."* [1](#0-0) 

In practice the mapping violates that binding:
```
Network::Nightly => 5665,
Network::TestNetworkWithForks => 5665,
``` [2](#0-1) 

In `run_l1_block`, the only network-distinguishing gate for a `DataOnDa::BatchProofMethodId` body is this equality check, followed by signature verification against `method_id_upgrade_authority_da_public_keys`:
```rust
let circuit_chain_id = citrea_network_to_chain_id(network);
if circuit_chain_id != batch_proof_method_id.body.chain_id {
    continue;
}
if !verify_method_id_security_council(
    *method_id_upgrade_authority_da_public_keys,
    batch_proof_method_id.body.serialize().as_slice(),
    batch_proof_method_id.signatures_with_index(),
) { continue; }
BatchProofMethodIdAccessor::<S>::insert(...);
``` [3](#0-2) 

Compounding this, the default (no env-var override) `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` constants for `TestNetworkWithForks` are hard-coded to the exact same 5 pubkeys/env-var names as `Nightly`: [4](#0-3) 

Because `verify_method_id_security_council` only checks the signatures against `msg = body.serialize()` (which includes `chain_id`) and the fixed pubkey set — with no domain separation tag for "which physical Network deployment" beyond the colliding `chain_id` — an identical `BatchProofMethodId` inscription (body bytes + 3-of-5 signatures) that was broadcast and finalized on Nightly's DA is byte-for-byte a valid, signature-passing inscription when replayed (inscribed again, with the attacker paying only Bitcoin fees) on whatever chain a TestNetworkWithForks-configured node/prover scans. The `activation_l2_height` monotonicity guard (`<= last_activation_height` check) does not prevent this since the attacker/anyone can simply resubmit the same signed body as long as it exceeds the target chain's currently stored last activation height — attacker doesn't even need to forge anything, just re-broadcast the exact public bytes.

No other guard in the circuit (e.g. `blob.sender()` checks used for `Aggregate`/`SequencerCommitment` variants) applies to `BatchProofMethodId`, so sender/pubkey identity is enforced solely through the council-signature check, which is defeated by the shared default keys plus the shared `chain_id`.

### Impact Explanation
If a TestNetworkWithForks deployment uses the default (unoverridden) constants — which is the out-of-the-box behavior unless every one of the 5 `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_{1..5}` env vars is explicitly overridden with distinct keys — a genuine Nightly-only batch-proof method-id upgrade is silently and unauthorizedly applied to TestNetworkWithForks (or vice versa) via `BatchProofMethodIdAccessor::insert`. This changes which zk method id is trusted to verify all subsequent batch proofs on the affected network from that activation height onward, matching the explicitly listed Critical category "a sequencer commitment or method-id upgrade accepted that was never authorised." The change is deterministic and repeatable for every future Nightly (or TestNetworkWithForks) council upgrade as long as the default-key/chain-id collision remains, and it affects every honest full node and light client prover on the victim network identically (since it's baked into the circuit's deterministic, provable logic) — it is not merely a local inconsistency but a proven, canonical state change.

### Likelihood Explanation
This requires only that a TestNetworkWithForks deployment be run with default council keys (the repo's own constants make this the default unless every key is overridden via env vars at build time) and that the two networks' DA scanning targets can receive the same publicly-broadcast inscription bytes. The attacker's cost is limited to Bitcoin inscription/mining fees to resubmit already-public bytes (explicitly an allowed attacker capability), with no need for any privileged key, council access, or hash-rate majority. Whether this is exploitable in a specific live deployment depends on operational configuration (whether the deploying team actually overrides all 5 keys and/or the chain_id) which I could not fully verify from the indexed files alone, but the code-level guarantee that is documented ("prevent cross network replay attacks") is objectively broken by the constant table itself, independent of any specific runtime configuration.

### Recommendation
- Assign `TestNetworkWithForks` its own unique chain_id distinct from `Nightly` in `citrea_network_to_chain_id`.
- Ensure `TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` defaults to keys distinct from Nightly's, or require these to be mandatorily supplied (no silent default) for any non-Nightly network sharing infrastructure.
- Consider binding the signed message to more than `chain_id` (e.g., include the DA network identifier/magic bytes and a version indicator) to remove any possibility of numeric collisions providing false cross-network authorization.

### Proof of Concept
```rust
// crates/light-client-prover/src/circuit/mod.rs (test module)
#[test]
fn test_batch_proof_method_id_replay_across_nightly_and_test_network_with_forks() {
    // Binding under test:
    // citrea_network_to_chain_id(Network::Nightly) == citrea_network_to_chain_id(Network::TestNetworkWithForks)
    assert_eq!(
        citrea_network_to_chain_id(Network::Nightly),
        citrea_network_to_chain_id(Network::TestNetworkWithForks)
    );

    // Build one BatchProofMethodId body/signatures using Nightly's chain_id and
    // the (default) council keys shared by both networks.
    let body = BatchProofMethodIdBody {
        method_id: [9u32; 8],
        activation_l2_height: 999, // higher than any inserted entry on either network
        chain_id: citrea_network_to_chain_id(Network::Nightly),
    };
    let msg = body.serialize();
    let prehash = eip191_hash_message(msg.as_slice());
    let (_pubkeys, signers) = generate_initial_pub_keys_with_signers(); // same defaults for both
    let signatures_with_index = create_valid_signatures(&signers, &prehash);
    let batch_proof_method_id = BatchProofMethodId { body, signatures_with_index };

    // Run once with network = Nightly: insert succeeds (expected, authorized).
    let result_nightly = run_l1_block_with_blob(Network::Nightly, &batch_proof_method_id);
    assert!(result_nightly.contains_inserted(999, [9u32; 8]));

    // Replay the IDENTICAL bytes with network = TestNetworkWithForks: insert also succeeds
    // (should NOT succeed, since council never authorized TestNetworkWithForks).
    let result_test_network = run_l1_block_with_blob(Network::TestNetworkWithForks, &batch_proof_method_id);
    assert!(result_test_network.contains_inserted(999, [9u32; 8])); // demonstrates the cross-network replay
}
```
This test demonstrates both sides of the binding failing: the same signed body (same `chain_id` numeric value, same signatures) is accepted by `run_l1_block`/`BatchProofMethodIdAccessor::insert` on both `Network::Nightly` and `Network::TestNetworkWithForks`, despite the code's own stated goal of chain_id preventing exactly this cross-network replay.

### Citations

**File:** crates/light-client-prover/src/circuit/mod.rs (L529-565)
```rust
                DataOnDa::BatchProofMethodId(batch_proof_method_id) => {
                    log!("Found batch proof method id");
                    let batch_proof_method_ids =
                        BatchProofMethodIdAccessor::<S>::get(&mut working_set).unwrap();

                    let last_activation_height = batch_proof_method_ids
                        .last()
                        .expect("Should be at least one")
                        .0;

                    if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
                        log!("Batch proof method id activation height is not greater than the last one");
                        continue;
                    }

                    let circuit_chain_id = citrea_network_to_chain_id(network);
                    if circuit_chain_id != batch_proof_method_id.body.chain_id {
                        log!("Method ID upgrade transactions chain ID does not match circuit chain ID");
                        continue;
                    }

                    // Verify the signatures only if the activation height is greater than the last one
                    // This prevents replay attacks of old method IDs
                    if !verify_method_id_security_council(
                        *method_id_upgrade_authority_da_public_keys,
                        batch_proof_method_id.body.serialize().as_slice(),
                        batch_proof_method_id.signatures_with_index(),
                    ) {
                        log!("Method ID security council verification failed");
                        continue;
                    }

                    BatchProofMethodIdAccessor::<S>::insert(
                        batch_proof_method_id.body.activation_l2_height,
                        batch_proof_method_id.body.method_id,
                        &mut working_set,
                    );
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L758-771)
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
}
```

**File:** crates/light-client-prover/src/circuit/initial_values.rs (L500-646)
```rust
    /// Public keys of the method ID upgrade authority in the Bitcoin DA on Nightly.
    /// This public key is set at compile time via the `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY` environment variable.
    /// If the variable is not set, it defaults to a predefined value.
    /// 3 out of 5 signatures are required to upgrade method IDs.
    pub const NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS: [[u8;
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
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_2") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9076
                None => "03b15df91f38ec6e0520b71fca528780820e75541f3371f6389a4f77ad0e5b823e",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_2 must be valid 33-byte hex string"
                ),
            }
        },
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_3") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9075
                None => "03fb89fd189501b9f55863a8194a8daff5b684cc52c0c21092f02ce428374c59f7",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_3 must be valid 33-byte hex string"
                ),
            }
        },
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_4") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9074
                None => "037d415a6027c2dc598c3ee52e6e93e0b61dabf9ea224895533a4de34fef4b91e0",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_4 must be valid 33-byte hex string"
                ),
            }
        },
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_5") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9073
                None => "022fad5142da490bed9c86beda47fe8538ec184d12e39db55ebf3ec41d180352d0",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_5 must be valid 33-byte hex string"
                ),
            }
        },
    ];

    /// Public keys of the method ID upgrade authority in the Bitcoin DA on Test Network with Forks.
    /// This public key is set at compile time via the `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY` environment variable.
    /// If the variable is not set, it defaults to a predefined value.
    /// 3 out of 5 signatures are required to upgrade method IDs.
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
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_2") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9076
                None => "03b15df91f38ec6e0520b71fca528780820e75541f3371f6389a4f77ad0e5b823e",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_2 must be valid 33-byte hex string"
                ),
            }
        },
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_3") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9075
                None => "03fb89fd189501b9f55863a8194a8daff5b684cc52c0c21092f02ce428374c59f7",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_3 must be valid 33-byte hex string"
                ),
            }
        },
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_4") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9074
                None => "037d415a6027c2dc598c3ee52e6e93e0b61dabf9ea224895533a4de34fef4b91e0",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_4 must be valid 33-byte hex string"
                ),
            }
        },
        {
            let hex_pub_key = match option_env!("METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_5") {
                Some(k) => k,
                // Private key: 79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9073
                None => "022fad5142da490bed9c86beda47fe8538ec184d12e39db55ebf3ec41d180352d0",
            };
            match const_hex::const_decode_to_array(hex_pub_key.as_bytes()) {
                Ok(pk) => pk,
                Err(_) => panic!(
                    "METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_5 must be valid 33-byte hex string"
                ),
            }
        },
    ];
```
