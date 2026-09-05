### Title
StackerDB chunk signatures omit the StackerDB's smart-contract identifier, allowing cross-contract replay of validly-signed chunks - (File: libstackerdb/src/libstackerdb.rs)

### Summary
`SlotMetadata::auth_digest` — the digest a signer signs to authenticate a StackerDB chunk write — commits only to `(slot_id, slot_version, data_hash)`. It never commits to the StackerDB's `smart_contract_id` (the `QualifiedContractIdentifier` of the specific `.signers-*`, `.miners`, or mock-signer StackerDB instance). This is the same missing-domain-separator class of bug as the reported `P2pLendingProxy.isValidSignature` issue: a signature that is valid for one "instance" (there, a proxy contract; here, a StackerDB contract) is also valid for any other instance that assigns the same signer the same `slot_id`, as long as an old `(slot_id, slot_version, data_hash)` tuple can be reproduced.

### Finding Description
`SlotMetadata::auth_digest` (`libstackerdb/src/libstackerdb.rs:159-166`) computes:
```rust
fn auth_digest(&self) -> Sha512Trunc256Sum {
    let mut hasher = Sha512_256::new();
    hasher.update(self.slot_id.to_be_bytes());
    hasher.update(self.slot_version.to_be_bytes());
    hasher.update(self.data_hash.0);
    Sha512Trunc256Sum::from_hasher(hasher)
}
``` [1](#0-0) 

`SlotMetadata::verify` (`libstackerdb/src/libstackerdb.rs:181-193`) recovers the public key from this digest and checks it hashes to the expected `StacksAddress`, with no reference to which StackerDB (contract) the chunk belongs to: [2](#0-1) 

The contract identity is supplied only out-of-band by the *caller*: `StackerDBSync::validate_received_chunk` looks up `get_slot_signer(smart_contract_id, data.slot_id)` and then calls `slot_metadata.verify(&addr)` — the `smart_contract_id` is used purely to select which address's key should have signed, but is never folded into the signed digest itself: [3](#0-2) 

Consequently, if a signer's Stacks address is assigned the same `slot_id` in two different StackerDB contracts (this is the normal case: `.signers-0-N`/`.signers-1-N` per reward cycle, or `.miners`, all reuse a stable slot-to-signer/miner mapping), a chunk `(slot_id, slot_version, sig)` that was validly signed and published to StackerDB A can be re-submitted verbatim to StackerDB B. `slot_metadata.verify()` will pass in B because the digest never distinguished "A" from "B" — exactly analogous to the reported bug where a signature valid for one `P2pLendingProxy` was valid for all of a user's proxies because the contract address was never part of the signed payload.

### Impact Explanation
This breaks the equality "a chunk accepted into StackerDB X was actually authored/authorized for StackerDB X" — i.e., a validation verdict that different nodes could disagree on depending on which stale chunks they've seen replayed. Concretely: an old, validly-signed chunk from a prior reward cycle's `.signers-*` StackerDB (e.g., a stale `BlockResponse`/mock-signature/DKG-related payload that a signer previously published) could be replayed into the current reward cycle's StackerDB by the same signer (or anyone who has captured the old signed chunk, since chunk pushes are gossiped over the P2P StackerDB machinery) as long as slot_id/slot_version/data_hash line up or are reconstructable. Because the outer StackerDB-layer authentication provides no contract binding, nodes have no cryptographic way to reject an old chunk as "not intended for this StackerDB." This is a minority/single-signer-triggerable divergence in what different nodes consider a validly-authenticated chunk for a given StackerDB, which is the kind of "validation verdict two nodes disagree on" class called out as in-scope.

The severity is bounded because most consumer code paths that matter for consensus (block signatures, block rejections, mock proposals) re-verify an *inner* SIP-018/structured-data signature over content that itself includes `consensus_hash`/tenure-specific fields (see `structured_data_message_hash`, `NakamotoBlockHeader::signer_signature_hash_inner`), so a replayed StackerDB chunk carrying a block-related `SignerMessage` would typically still be rejected by that inner check. The practical exposure is therefore to StackerDB-layer semantics that rely solely on the outer chunk signature (e.g., which stale/mock data a StackerDB slot holds), rather than a direct fork or reward-theft path — this could not be fully verified against every consumer in the time available.

### Likelihood Explanation
Likelihood is moderate-to-low for direct consensus impact but the bug itself is trivially reachable: any signer who has ever published a chunk to any StackerDB instance possesses a signature that remains valid forever for the same `slot_id` in any other StackerDB where they hold that slot, with no expiry or contract binding. No privileged access or majority coordination is needed — this is purely a property of the signing scheme in `libstackerdb`.

### Recommendation
Include the StackerDB's `smart_contract_id` (`QualifiedContractIdentifier`) — and ideally the chain ID / network — in `SlotMetadata::auth_digest`, so that a chunk signature is cryptographically bound to the specific StackerDB instance it was written to, mirroring the `structured_data_message_hash`/EIP-712-style domain separation already used elsewhere in the codebase (e.g., `make_structured_data_domain` in `stackslib/src/util_lib/signed_structured_data.rs`). This closes the replay path at the point of signature verification rather than relying on incidental protections in higher-level message formats.

### Proof of Concept
1. Signer `S` with private key `sk` is assigned `slot_id = 3` in StackerDB contract `A` (e.g., `.signers-0-1` for reward cycle 0) and also `slot_id = 3` in StackerDB contract `B` (e.g., `.signers-1-1` for reward cycle 1) — this slot-index stability is the normal signer-set assignment scheme.
2. `S` legitimately signs and publishes chunk `(slot_id=3, slot_version=5, data=D)` to `A`. The signature is `sig = sign(sk, auth_digest(3, 5, sha512_256(D)))` per `libstackerdb/src/libstackerdb.rs:159-179`.
3. An attacker (or `S` itself, or anyone relaying observed network traffic) constructs `StackerDBChunkData { slot_id: 3, slot_version: 5, sig, data: D }` and pushes it to StackerDB `B`.
4. `StackerDBSync::validate_received_chunk` in `stackslib/src/net/stackerdb/mod.rs:649-718` looks up the signer address for slot 3 in `B` (which is also `S`), computes `slot_metadata = data.get_slot_metadata()`, and calls `slot_metadata.verify(&addr)`.
5. Because `auth_digest` never included `B`'s contract identifier, verification succeeds, and the chunk (originally authorized only for `A`) is accepted into `B` as if freshly and specifically authorized for `B`.

### Citations

**File:** libstackerdb/src/libstackerdb.rs (L159-166)
```rust
    /// Get the digest to sign that authenticates this chunk data and metadata
    fn auth_digest(&self) -> Sha512Trunc256Sum {
        let mut hasher = Sha512_256::new();
        hasher.update(self.slot_id.to_be_bytes());
        hasher.update(self.slot_version.to_be_bytes());
        hasher.update(self.data_hash.0);
        Sha512Trunc256Sum::from_hasher(hasher)
    }
```

**File:** libstackerdb/src/libstackerdb.rs (L181-193)
```rust
    /// Verify that a given principal signed this chunk metadata.
    /// Note that the address version is ignored.
    pub fn verify(&self, principal: &StacksAddress) -> Result<bool, Error> {
        let sigh = self.auth_digest();
        let pubk = StacksPublicKey::recover_to_pubkey_without_validating_low_s(
            sigh.as_bytes(),
            &self.signature,
        )
        .map_err(|ve| Error::VerifyingError(ve.to_string()))?;

        let pubkh = Hash160::from_node_public_key(&pubk);
        Ok(pubkh == *principal.bytes())
    }
```

**File:** stackslib/src/net/stackerdb/mod.rs (L679-697)
```rust
        // validate -- must be signed by the expected author
        let addr = match self
            .stackerdbs
            .get_slot_signer(smart_contract_id, data.slot_id)?
        {
            Some(addr) => addr,
            None => {
                return Ok(false);
            }
        };

        let slot_metadata = data.get_slot_metadata();
        if !slot_metadata.verify(&addr)? {
            info!(
                "StackerDBChunk for {} ID {} is not signed by {}",
                smart_contract_id, data.slot_id, &addr
            );
            return Ok(false);
        }
```
