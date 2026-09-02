### Title
Cross-network replay of council-signed `BatchProofMethodId` upgrade between `Nightly` and `TestNetworkWithForks` — ([File: crates/light-client-prover/src/circuit/mod.rs])

### Summary
`citrea_network_to_chain_id` maps both `Network::Nightly` and `Network::TestNetworkWithForks` to the identical chain id `5665`, and the two networks additionally ship identical default `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` constants. The `chain_id` field in a `BatchProofMethodIdBody` is meant to bind a council-signed method-id upgrade to exactly one network, but because the mapping collides, a genuinely council-signed and publicly-inscribed body from one network is a byte-for-byte valid, signature-verifying `BatchProofMethodId` transaction on the other.

### Finding Description
The claimed binding is: `citrea_network_to_chain_id(Network::Nightly) != citrea_network_to_chain_id(Network::TestNetworkWithForks)`. Tracing the source shows this is false: [1](#0-0) 

Both variants return `5665`. This constant is the only differentiator used in `run_l1_block` when processing a `DataOnDa::BatchProofMethodId(batch_proof_method_id)` blob: [2](#0-1) 

The only checks performed are: (1) `activation_l2_height` strictly increasing, (2) `circuit_chain_id != body.chain_id` (collides for these two networks), and (3) `verify_method_id_security_council` over `initial_da_pubkeys` = `method_id_upgrade_authority_da_public_keys`. Critically, there is **no `blob.sender()` check** for `BatchProofMethodId` (unlike `Complete`/`Aggregate`/`SequencerCommitment` blobs), so the transaction can be inscribed by anyone; validity rests entirely on the embedded signatures matching the constant pubkey set.

The default `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` for `Nightly` and `TestNetworkWithForks` are also identical (same env-var names/fallback hex keys): [3](#0-2) [4](#0-3) 

Exploit flow: the security council legitimately signs and inscribes a `BatchProofMethodId` body (with `chain_id = 5665`, some `method_id`/`activation_l2_height`) intended only for the `Nightly` deployment's DA channel. The raw inscription bytes (body + 3 valid signatures) are public once mined. An unprivileged attacker copies these exact bytes and inscribes an identical Bitcoin transaction targeted at the `TestNetworkWithForks` deployment's DA stream (paying only Bitcoin fees — no council key, no DA key, no privileged role required). The `TestNetworkWithForks` light client prover's `run_l1_block` parses this blob as `DataOnDa::BatchProofMethodId`, computes `circuit_chain_id = citrea_network_to_chain_id(Network::TestNetworkWithForks) = 5665`, finds it equal to `body.chain_id = 5665`, and successfully verifies the signatures against the (identical) default council pubkeys — accepting a method-id upgrade that the council never authorized for `TestNetworkWithForks`.

No existing guard stops this: `verify_method_id_security_council` only checks signatures against the message bytes and the constant pubkeys, with no network binding beyond the collided `chain_id` field; there is no sender/pubkey-based provenance check for this blob type.

### Impact Explanation
`BatchProofMethodIdAccessor` state on `TestNetworkWithForks` is corrupted with a method-id upgrade transplanted from `Nightly`, changing which zkVM method id is accepted for verifying batch proofs from `activation_l2_height` onward. If honest light-client provers for `TestNetworkWithForks` process this replayed transaction while honest provers who reject it (e.g., due to differing local configuration or a future fix) do not, provers diverge on `BatchProofMethodIdAccessor` state and thus on which batch proofs verify — a method-id upgrade accepted that was never authorized for that network, matching the Critical category "a sequencer commitment or method-id upgrade accepted that was never authorised" / provers committing different outputs for the same L1 block. The attack is repeatable for every legitimately-signed Nightly (or vice versa) method-id upgrade message, at the cost of one Bitcoin inscription fee per replay.

### Likelihood Explanation
Preconditions: a security-council-signed `BatchProofMethodId` body must exist and be publicly observable on `Nightly`'s DA (which it will be, since that's how legitimate upgrades are distributed), and `TestNetworkWithForks` deployment must use default/matching `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` (the shipped defaults do match). The attacker only needs to observe a public inscription and re-inscribe identical bytes on the target network's DA, paying ordinary Bitcoin fees — fully within the stated unprivileged-attacker capabilities. No mainnet or live Clementine involvement is required, and no cryptographic forgery is needed since the signatures are copied verbatim.

### Recommendation
Assign `Nightly` and `TestNetworkWithForks` distinct chain ids in `citrea_network_to_chain_id` (e.g. `5666` for `TestNetworkWithForks`), and additionally ensure `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` (and other security-critical DA pubkeys) differ per network by default rather than sharing fallback constants, so that a chain-id collision alone cannot enable cross-network signature replay.

### Proof of Concept
```rust
// crates/light-client-prover/src/circuit/tests_chain_id_collision.rs
use sov_rollup_interface::Network;
use crate::circuit::citrea_network_to_chain_id;

#[test]
fn nightly_and_test_network_with_forks_chain_ids_must_differ() {
    let nightly_id = citrea_network_to_chain_id(Network::Nightly);
    let test_net_id = citrea_network_to_chain_id(Network::TestNetworkWithForks);
    // This assertion FAILS on current source: both equal 5665
    assert_ne!(nightly_id, test_net_id,
        "Network::Nightly and Network::TestNetworkWithForks must not share a chain id, \
         otherwise a council-signed BatchProofMethodId body for one network is replayable on the other");
}
```
Follow-up integration proof (conceptual, to be run as a background Devin task): construct a `BatchProofMethodIdBody{ chain_id: citrea_network_to_chain_id(Network::Nightly), .. }`, sign it with `generate_initial_pub_keys_with_signers()`/`create_valid_signatures` (as in `method_id_verifier.rs` tests), wrap it in `DataOnDa::BatchProofMethodId`, and feed it as a blob into `LightClientProofCircuit::run_l1_block` invoked with `network = Network::TestNetworkWithForks` and `method_id_upgrade_authority_da_public_keys = bitcoinda::TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS`. Assert that `BatchProofMethodIdAccessor` is updated afterward, proving acceptance of a body signed/intended only for `Nightly`.

### Citations

**File:** crates/light-client-prover/src/circuit/mod.rs (L529-559)
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
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L763-771)
```rust
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

**File:** crates/light-client-prover/src/circuit/initial_values.rs (L500-572)
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
```

**File:** crates/light-client-prover/src/circuit/initial_values.rs (L574-646)
```rust
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
