### Title
Production `SignatureManager` uses a hardcoded, publicly known ECDSA private key for peer-identity and consensus precommit-vote signing - ([File: crates/apollo_signature_manager/src/signature_manager.rs])

### Summary
The production entry point `create_signature_manager()` instantiates the node's signing component using `LocalKeyStore::new_for_testing()`, which contains a hardcoded ECDSA private key constant baked directly into source. There is no separate, non-test path used in `apollo_node`. Anyone with access to the public repository (i.e., everyone) knows this private key and can produce valid signatures under the identity that this key store represents. This mirrors the Dragonfly2 hardcoded JWT signing key issue: a cryptographic secret meant to authenticate/authorize protected actions is checked into source code rather than derived from a securely managed, per-instance secret.

### Finding Description
`LocalKeyStore::new_for_testing()` defines a `const PRIVATE_KEY` literal: [1](#0-0) 

This key store is wired into the production `SignatureManager` alias without any test-only gating: [2](#0-1) 

And the crate's own production factory function calls it directly, with a TODO acknowledging the key-management gap is unresolved: [3](#0-2) 

`create_signature_manager()` is wired into the node's component bootstrap in `apollo_node`, i.e., it is the mechanism used to build the node's live signing component, not a test harness.

The `SignatureManager` produced from this hardcoded key is used to sign two message types:
- `sign_identification` — signs a peer-identity challenge (`INIT_PEER_ID` domain), used to authenticate a peer's identity claim, per `build_peer_identity_message_digest`.
- `sign_precommit_vote` — signs a `BlockHash` (`PRECOMMIT_VOTE` domain) for consensus precommit voting. [4](#0-3) [5](#0-4) 

Because the private key is a compile-time constant identical across every deployment that hasn't replaced this code path, any party who reads the public source can forge:
1. Valid peer-identity signatures for the node's `INIT_PEER_ID` challenge, impersonating the node in peer-authentication handshakes.
2. Valid precommit-vote signatures over arbitrary `BlockHash` values, i.e., forge this node's consensus vote for any block hash of the attacker's choosing.

### Impact Explanation
If any deployed sequencer node relies on this default `create_signature_manager()` path (as its TODO comment suggests may currently be the case — "understand how key store would look in production"), an external attacker who knows the hardcoded key can:
- Impersonate the node's cryptographic identity during peer/consensus identification handshakes.
- Forge precommit votes attributed to that node for any block hash, directly corrupting the honest-vote tally used to finalize blocks — this can cause honest-node divergence or a wrong committed block hash, and in the worst case contribute to a network unable to safely confirm new blocks if enough forged votes are injected or if this undermines the integrity guarantee that votes are unforgeable.

This satisfies the "wrong committed root or block hash" / "honest-node divergence" impact bar from the validation rules, since the entire security assumption of the precommit signature scheme (ECDSA signatures unforgeable without the private key) is broken by a key that is not actually private.

### Likelihood Explanation
The severity depends entirely on whether any running deployment actually uses `create_signature_manager()` unmodified in production — the crate's own TODO comment explicitly flags this as unresolved ("understand how key store would look in production and better define the way the signature manager is created"), indicating this is a real, currently-open gap rather than a hypothetical. No exploitation prerequisites are required beyond reading the public source code to obtain the key; no privileged access, node compromise, or insider knowledge is needed.

### Recommendation
- Remove any production code path that can resolve to `LocalKeyStore::new_for_testing()`. Rename/gate that constructor behind `#[cfg(test)]` so it cannot be reached from `create_signature_manager()` or any non-test binary.
- Require `create_signature_manager()` (and any real `KeyStore` used by `apollo_node`) to source key material from a secure, per-instance secret (e.g., HSM, KMS, encrypted keystore file, environment-injected secret) rather than a compiled-in constant.
- Add a build-time or startup assertion that fails if the production key store's key matches the well-known testing constant, to catch any regression of this kind.

### Proof of Concept
1. Read the public repository and extract the constant from `signature_manager.rs`:
   `PRIVATE_KEY = PrivateKey(Felt::from_hex_unchecked("0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133"))`.
2. Using `starknet_core::crypto::ecdsa_sign`/`get_public_key` (the same primitives used by `LocalKeyStore`/`ecdsa_sign` in `signature_manager.rs`), compute the corresponding public key `0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a`.
3. For any target `BlockHash`, construct `build_precommit_vote_message_digest(block_hash)` exactly as the node does (`PRECOMMIT_VOTE` domain separator + block hash bytes, hashed with `blake2s_to_felt`), then sign it with the extracted private key using `ecdsa_sign`.
4. The resulting signature will verify successfully against the public key associated with any node instance that still uses the default `create_signature_manager()`/`LocalKeyStore::new_for_testing()` path, allowing the attacker to forge that node's consensus precommit vote for an arbitrary block hash, or forge its peer-identity signature for any challenge.

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L59-82)
```rust
    pub async fn sign_identification(
        &self,
        peer_id: PeerId,
        challenge: Challenge,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_peer_identity_message_digest(peer_id, challenge);
        self.sign(message_digest).await
    }

    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
    }

    async fn sign(&self, message_digest: MessageDigest) -> SignatureManagerResult<RawSignature> {
        let private_key = self.keystore.get_key().await?;
        let signature = ecdsa_sign(&private_key, &message_digest)
            .map_err(|e| SignatureManagerError::Sign(e.to_string()))?;

        Ok(signature.into())
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L101-111)
```rust
    pub(crate) const fn new_for_testing() -> Self {
        // Created using `cairo-lang`.
        const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
            "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
        ));
        const PUBLIC_KEY: PublicKey = PublicKey(Felt::from_hex_unchecked(
            "0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a",
        ));

        Self { private_key: PRIVATE_KEY, public_key: PUBLIC_KEY }
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L127-145)
```rust
fn build_peer_identity_message_digest(peer_id: PeerId, challenge: Challenge) -> MessageDigest {
    let challenge = &challenge.0;
    let peer_id = peer_id.to_bytes();
    let mut message = Vec::with_capacity(INIT_PEER_ID.len() + peer_id.len() + challenge.len());
    message.extend_from_slice(INIT_PEER_ID);
    message.extend_from_slice(&peer_id);
    message.extend_from_slice(challenge);

    MessageDigest(blake2s_to_felt(&message))
}

fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);

    MessageDigest(blake2s_to_felt(&message))
}
```

**File:** crates/apollo_signature_manager/src/lib.rs (L14-27)
```rust
#[derive(Clone, Debug)]
pub struct LocalKeyStoreSignatureManager(pub GenericSignatureManager<LocalKeyStore>);

impl LocalKeyStoreSignatureManager {
    pub fn new() -> Self {
        Self(GenericSignatureManager::new(LocalKeyStore::new_for_testing()))
    }
}

impl Default for LocalKeyStoreSignatureManager {
    fn default() -> Self {
        Self::new()
    }
}
```

**File:** crates/apollo_signature_manager/src/lib.rs (L39-43)
```rust
// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
}
```
