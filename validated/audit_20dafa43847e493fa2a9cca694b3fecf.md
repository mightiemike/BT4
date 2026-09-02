## Analysis

The claim requires: `citrea_network_to_chain_id(Network::Nightly) != citrea_network_to_chain_id(Network::TestNetworkWithForks)`. This is confirmed **false** — both map to `5665`. [1](#0-0) 

However, the security council public keys used in `verify_method_id_security_council` are also network-scoped, via `NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` and `TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS`. [2](#0-1) 

Critically, both constant sets fall back to the **exact same default hex-encoded public keys** whenever the corresponding `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_{1..5}` environment variables are unset at compile time — the same private keys (`79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D907{3..7}`) are hardcoded as the default for *both* Nightly and TestNetworkWithForks. [3](#0-2) [4](#0-3) 

`InitialValueProvider<BitcoinSpec>::method_id_upgrade_authority_da_public_keys` dispatches on `Network` to select one of these constant arrays, which is what gets passed into `run_l1_block` as `method_id_upgrade_authority_da_public_keys`. [5](#0-4) 

In `run_l1_block`'s `DataOnDa::BatchProofMethodId` arm, the chain-id check and the signature check are both performed against the caller-supplied `network` parameter's constants: [6](#0-5) 

## Conclusion

Given these defaults, whether the attack is exploitable in a live deployment depends entirely on **deployment-time configuration**, which is outside the scope of what this repository's code enforces:

1. If a real Nightly and a real TestNetworkWithForks deployment both compile with the default (unset) `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_{1..5}` env vars, then both the `chain_id` check (5665 == 5665) and `verify_method_id_security_council` (identical default pubkeys) would pass, and a `BatchProofMethodId` inscription authorized for one network's method-id upgrade would be accepted by the other — an authority-bypass consistent with the claim.
2. If real deployments set distinct `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_*` env vars per network (which the code comments imply is the intended operational practice — "This public key is set at compile time via the environment variable... If the variable is not set, it defaults to a predefined value"), then `verify_method_id_security_council` would independently reject cross-network replay because the signatures would fail to verify against the *other* network's distinct pubkeys, even though the `chain_id` check alone is bypassed.

The repository code and index do not contain evidence of what environment variables are actually set for a live Nightly vs. a live TestNetworkWithForks deployment (these are build-time secrets/config not present in the indexed source), so I cannot confirm with certainty that both networks share identical council keys in production. The `chain_id` collision itself is a real, demonstrable code-level defect independent of key configuration — it is a documented "cross network replay attack" prevention mechanism per the doc comment on `citrea_network_to_chain_id` that is broken for exactly the Nightly/TestNetworkWithForks pair, weakening defense-in-depth even if the pubkey check happens to also differ in a given deployment. [7](#0-6) 

### Title
Chain-id collision between `Network::Nightly` and `Network::TestNetworkWithForks` breaks method-id-upgrade chain isolation - (crates/light-client-prover/src/circuit/mod.rs)

### Summary
`citrea_network_to_chain_id` maps both `Network::Nightly` and `Network::TestNetworkWithForks` to `5665`, so the chain-id check in `run_l1_block`'s `DataOnDa::BatchProofMethodId` handler cannot distinguish these two networks. This defeats the documented purpose of the function ("used ... to prevent cross network replay attacks"), though full exploitability additionally depends on whether the two networks are configured with identical security-council public keys at build time — a fact not verifiable from the indexed source.

### Finding Description
The broken binding: `citrea_network_to_chain_id(Network::Nightly) == citrea_network_to_chain_id(Network::TestNetworkWithForks) == 5665`, when the function's own doc comment states it exists specifically "to prevent cross network replay attacks" between named networks. [8](#0-7) 

In `run_l1_block`, the `chain_id` check compares `circuit_chain_id = citrea_network_to_chain_id(network)` against `batch_proof_method_id.body.chain_id`; because both networks compute the same constant, an inscription signed and valid on one network passes this gate unmodified on the other. [9](#0-8) 

The remaining defense is `verify_method_id_security_council`, which checks EIP-191 signatures against `method_id_upgrade_authority_da_public_keys` — a network-specific array supplied by the caller/host via `InitialValueProvider::method_id_upgrade_authority_da_public_keys`. [10](#0-9) [5](#0-4) 

The default (compile-time-fallback) values for `NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` and `TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` are byte-identical, both defaulting to the same five hardcoded test private keys, unless operators explicitly set distinct `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_{1..5}` environment variables per deployment. [3](#0-2) [4](#0-3) 

If a deployment relies on these defaults for both networks (or otherwise reuses the same council keys), an attacker who observes a legitimately council-signed `BatchProofMethodId` inscription on Nightly's DA can re-inscribe the identical bytes into a Bitcoin chain also scanned by a TestNetworkWithForks light client prover; both the chain-id check and signature check pass identically, and `BatchProofMethodIdAccessor::insert` accepts the upgrade. [11](#0-10) 

### Impact Explanation
If exploitable (i.e., shared council keys across the two networks in a given deployment), a method-id upgrade authorized for one Citrea network is accepted as authorized for another — allowing a batch-proof method id (verification circuit) belonging to one network to become trusted on the other, potentially letting a false state transition be "proved" and accepted by that network's light client, i.e. AUTHORITY bypass on `BatchProofMethodId` upgrades. This matches the Critical category "a light client proof accepted for a state transition that did not happen" / "a method-id upgrade accepted that was never authorised." The blast radius is bounded to deployments where Nightly and TestNetworkWithForks share both the DA chain being scanned and identical council keys.

### Likelihood Explanation
Exploitability strictly requires that a live Nightly deployment and a live TestNetworkWithForks deployment (a) scan the same Bitcoin chain, and (b) use identical `METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEY_*` configuration (or rely on the shared compiled-in defaults). `TestNetworkWithForks` is documented as "used for testing purposes only," which may mean it is typically run only against regtest/private chains distinct from Nightly's chain, reducing real-world likelihood — but this is a deployment/config fact not present in the repo. If both conditions hold, the attacker's cost is only Bitcoin inscription fees for re-broadcasting existing, already-public bytes (no privileged key needed), and the attack is trivially repeatable for every legitimate Nightly method-id upgrade.

### Recommendation
Assign a distinct chain_id constant to `Network::TestNetworkWithForks` (e.g. a value other than 5665) in `citrea_network_to_chain_id`, and/or ensure `TEST_NETWORK_WITH_FORKS_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` cannot silently default to the same keys as `NIGHTLY_METHOD_ID_UPGRADE_AUTHORITY_DA_PUBLIC_KEYS` (e.g., require explicit distinct env vars or fail to compile when both are unset for non-Mainnet networks).

### Proof of Concept
```rust
// crates/light-client-prover/src/circuit/method_id_verifier.rs (or a new test module)
#[test]
fn chain_id_collision_nightly_vs_test_network_with_forks() {
    assert_eq!(
        citrea_network_to_chain_id(Network::Nightly),
        citrea_network_to_chain_id(Network::TestNetworkWithForks)
    ); // demonstrates broken binding
}
```
Full end-to-end proof requires constructing two `run_l1_block` calls (one with `Network::Nightly`, one with `Network::TestNetworkWithForks`) using the same signed `BatchProofMethodId` bytes and the *same* `method_id_upgrade_authority_da_public_keys` array (reflecting the shared-default configuration), asserting `BatchProofMethodIdAccessor::get` reflects the injected method id on both runs. This additional precondition (shared council keys) could not be confirmed from the repository alone and should be verified against actual Nightly/TestNetworkWithForks deployment configs before treating this as a confirmed live exploit.

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

**File:** crates/light-client-prover/src/circuit/method_id_verifier.rs (L14-20)
```rust
pub fn verify_method_id_security_council(
    initial_da_pubkeys: [[u8; SECURITY_COUNCIL_COMPRESSED_PUBKEY_SIZE];
        SECURITY_COUNCIL_MEMBER_COUNT],
    msg: &[u8],
    signatures_with_idx: &[([u8; SECURITY_COUNCIL_SIGNATURE_SIZE], u8);
         SECURITY_COUNCIL_SIGNATURE_THRESHOLD],
) -> bool {
```
