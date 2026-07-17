### Title
Unauthenticated `ContractCodeResponse::V1` Accepted Without Signature Verification Before `SignedContractCodeResponse` Feature Activation — (File: `chain/client/src/stateless_validation/validate.rs`)

### Summary

`validate_contract_code_response` gates signature enforcement behind `ProtocolFeature::SignedContractCodeResponse` (protocol version 85). Before that feature activates, any network peer can send a `ContractCodeResponse::V1` (Borsh discriminant `0`) carrying arbitrary contract code bytes for a currently-relevant chunk. The bytes pass validation and are stored in the chunk validator's `partial_witness_tracker` cache without any producer authentication, directly influencing stateless validation. This is the nearcore analog of the external report's pattern: untrusted data from a less-trusted entity is passed into a security-critical context (stateless validation) without validation, because the authentication check is gated behind a feature that has not yet activated.

### Finding Description

**Root cause — `validate_contract_code_response`:**

```rust
// chain/client/src/stateless_validation/validate.rs:369-382
pub fn validate_contract_code_response(...) -> Result<ChunkRelevance, Error> {
    let key = response.chunk_production_key();
    require_relevant!(validate_chunk_relevant(epoch_manager, key, store)?);
    let protocol_version = epoch_manager.get_epoch_protocol_version(&key.epoch_id)?;
    if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
        validate_witness_contract_code_response_signature(epoch_manager, response)?;
    }
    Ok(ChunkRelevance::Relevant)
}
```

When `protocol_version < 85`, the `if` branch is skipped entirely. A `ContractCodeResponse::V1` (which carries no `responder` field and no signature) passes this function and is treated as `Relevant`. [1](#0-0) 

**Unsigned V1 variant — `ContractCodeResponse`:**

`ContractCodeResponse::V1` has Borsh discriminant `0` and contains only `next_chunk` + `compressed_contracts` — no `responder`, no `signature`. `verify_signature` on V1 unconditionally returns `false`; `responder()` returns `None`. [2](#0-1) 

**No receive-side version gate for `ContractCodeResponse`:**

`handle_contract_code_response` has no `should_drop_for_version` pre-check (unlike `handle_chunk_contract_accesses` and `handle_partial_encoded_contract_deploys`, which both call `self.should_drop_for_version(...)` before validation). The only protection is inside `validate_contract_code_response`, which is bypassed pre-feature. [3](#0-2) 

**Contrast — `ChunkContractAccesses` has a receive-side gate:**

```rust
if self.should_drop_for_version(
    &accesses.chunk_production_key(),
    matches!(&accesses, ChunkContractAccesses::V2(_)),
    "chunk contract accesses",
) { return Ok(()); }
```

`ContractCodeResponse` has no equivalent gate. [4](#0-3) 

**Exploit path:**

1. Attacker observes a `ContractCodeRequest` from a chunk validator (or predicts the `ChunkProductionKey` from public chain data).
2. Before the legitimate chunk producer's V2 response arrives, attacker sends `ContractCodeResponse::V1` (Borsh: `[0x00] || next_chunk_bytes || compressed_contracts_bytes`) with arbitrary contract code bytes.
3. `validate_contract_code_response` passes (no signature check, `validate_chunk_relevant` passes for a current chunk).
4. `handle_contract_code_response` calls `response.decompress_contracts()` and then `partial_witness_tracker.store_accessed_contract_codes(key, contracts)`.
5. The wrong bytes are stored. `store_accessed_contract_codes` calls `process_update` with `create_if_not_exists: false`, meaning it updates an existing cache entry. The first call wins; the legitimate V2 response arriving later updates the same entry.
6. When the validator assembles the state witness, it uses the injected contract code, causing compilation failure or wrong execution results, and the validator fails to endorse the chunk. [5](#0-4) 

**Feature activation version:**

`SignedContractCodeResponse` is at protocol version 85 (stable). `MIN_SUPPORTED_PROTOCOL_VERSION` is 84. The binary still processes epochs at protocol version 84. [6](#0-5) [7](#0-6) 

### Impact Explanation

**Severity: High.** A malicious peer (no privileged role required — any network participant) can inject arbitrary contract code bytes into a chunk validator's stateless-validation cache for any currently-relevant chunk. The injected bytes cause the validator to fail witness validation (wrong contract execution results or compilation failure), preventing it from endorsing the chunk. If enough validators are targeted simultaneously, the chunk fails to accumulate the required endorsements, stalling chunk production for the affected shard. This is a targeted, shard-specific DoS on stateless validation.

The invariant broken is: *contract code delivered to a chunk validator for stateless validation must be authenticated as originating from the legitimate chunk producer*. Before `SignedContractCodeResponse` activates, this invariant does not hold — the code bytes are accepted from any peer.

### Likelihood Explanation

**Moderate.** `SignedContractCodeResponse` is at protocol version 85; the current stable is 86. On mainnet the feature is now active, so the window is the 84→85 upgrade epoch (already passed). However:

- `MIN_SUPPORTED_PROTOCOL_VERSION = 84` means the binary still processes epochs at version 84 (archival nodes, testnets, or any network that has not yet upgraded past 84).
- During the upgrade epoch (when the epoch protocol version is still 84 but the binary supports 85), the window is open.
- The attack requires no privileged access: any peer that can send a `ContractCodeResponse` network message can exploit this.
- The `ChunkProductionKey` needed to target a specific chunk is public information derivable from chain data.
- The attacker must win a race against the legitimate chunk producer's response, which is feasible for a well-connected peer.

### Recommendation

1. **Add a receive-side version gate** in `handle_contract_code_response` analogous to the one in `handle_chunk_contract_accesses`:

   ```rust
   fn handle_contract_code_response(&self, response: ContractCodeResponse) -> Result<(), Error> {
       // Drop V1 when SignedContractCodeResponse is active; drop V2 before it.
       let key = response.chunk_production_key();
       let version = self.epoch_manager.get_epoch_protocol_version(&key.epoch_id).ok();
       let is_v2 = matches!(&response, ContractCodeResponse::V2(_));
       if let Some(v) = version {
           let feature_active = ProtocolFeature::SignedContractCodeResponse.enabled(v);
           if is_v2 != feature_active {
               return Ok(());
           }
       }
       // ... existing validation
   }
   ```

2. **Verify contract code bytes against their expected `CodeHash`** before storing them in the cache, regardless of whether the response is signed. This provides defense-in-depth: even if an unauthenticated V1 response is accepted, wrong bytes would be detected at hash-verification time rather than at execution time.

3. **Consider retiring `ContractCodeResponse::V1`** from the Borsh enum once the minimum supported protocol version advances past 85, to eliminate the unsigned code path entirely.

### Proof of Concept

**Precondition:** Network epoch protocol version is 84 (pre-`SignedContractCodeResponse`).

**Divergent Borsh bytes:** Craft `ContractCodeResponse::V1`:
```
[0x00]                          // Borsh discriminant: V1
[next_chunk_borsh_bytes]        // ChunkProductionKey matching a live chunk
[compressed_contracts_borsh]    // Arbitrary/wrong contract code bytes
```

**Steps:**
1. Monitor the network for `ContractCodeRequest` messages from chunk validators (public P2P traffic).
2. Extract the `ChunkProductionKey` and `requester` from the observed request.
3. Immediately send the crafted `ContractCodeResponse::V1` directly to the `requester` validator, before the legitimate chunk producer responds.
4. `validate_contract_code_response` returns `Ok(Relevant)` — no signature check.
5. `handle_contract_code_response` decompresses the injected bytes and calls `store_accessed_contract_codes(key, injected_bytes)`.
6. The validator's cache now holds wrong contract code for the targeted chunk.
7. When the validator attempts to validate the state witness, it uses the wrong code, fails validation, and does not endorse the chunk.
8. Repeat for multiple validators to prevent the chunk from reaching the endorsement threshold.

**Expected outcome:** The targeted chunk fails to accumulate sufficient endorsements, causing it to be skipped or the shard to stall, constituting a DoS on stateless validation for the affected shard.

### Citations

**File:** chain/client/src/stateless_validation/validate.rs (L369-382)
```rust
pub fn validate_contract_code_response(
    epoch_manager: &dyn EpochManagerAdapter,
    response: &ContractCodeResponse,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let key = response.chunk_production_key();
    require_relevant!(validate_chunk_relevant(epoch_manager, key, store)?);
    let protocol_version = epoch_manager.get_epoch_protocol_version(&key.epoch_id)?;
    if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
        validate_witness_contract_code_response_signature(epoch_manager, response)?;
    }

    Ok(ChunkRelevance::Relevant)
}
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L372-422)
```rust
pub enum ContractCodeResponse {
    V1(ContractCodeResponseV1) = 0,
    V2(ContractCodeResponseV2) = 1,
}

impl ContractCodeResponse {
    pub fn encode(
        next_chunk: ChunkProductionKey,
        contracts: &Vec<CodeBytes>,
        signer: &ValidatorSigner,
        protocol_version: ProtocolVersion,
    ) -> std::io::Result<Self> {
        if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
            ContractCodeResponseV2::encode(next_chunk, contracts, signer).map(Self::V2)
        } else {
            ContractCodeResponseV1::encode(next_chunk, contracts).map(Self::V1)
        }
    }

    pub fn chunk_production_key(&self) -> &ChunkProductionKey {
        match self {
            Self::V1(v1) => &v1.next_chunk,
            Self::V2(v2) => &v2.inner.next_chunk,
        }
    }

    /// Account that produced this response. Available only for signed variants.
    pub fn responder(&self) -> Option<&AccountId> {
        match self {
            Self::V1(_) => None,
            Self::V2(v2) => Some(&v2.inner.responder),
        }
    }

    pub fn decompress_contracts(&self) -> std::io::Result<Vec<CodeBytes>> {
        let compressed_contracts = match self {
            Self::V1(v1) => &v1.compressed_contracts,
            Self::V2(v2) => &v2.inner.compressed_contracts,
        };
        compressed_contracts.decode().map(|(data, _size)| data)
    }

    /// Verifies the signature for signed variants. Returns `false` for
    /// unsigned variants since there is nothing to verify.
    pub fn verify_signature(&self, public_key: &PublicKey) -> bool {
        match self {
            Self::V1(_) => false,
            Self::V2(v2) => v2.verify_signature(public_key),
        }
    }
}
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L865-875)
```rust
    pub(super) fn handle_chunk_contract_accesses(
        &self,
        accesses: ChunkContractAccesses,
    ) -> Result<(), Error> {
        if self.should_drop_for_version(
            &accesses.chunk_production_key(),
            matches!(&accesses, ChunkContractAccesses::V2(_)),
            "chunk contract accesses",
        ) {
            return Ok(());
        }
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L1210-1224)
```rust
    /// Handles contract code responses message from chunk producer.
    fn handle_contract_code_response(&self, response: ContractCodeResponse) -> Result<(), Error> {
        if !validate_contract_code_response(
            self.epoch_manager.as_ref(),
            &response,
            self.runtime.store(),
        )?
        .is_relevant()
        {
            return Ok(());
        }
        let key = response.chunk_production_key().clone();
        let contracts = response.decompress_contracts()?;
        self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
    }
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs (L391-399)
```rust
    pub fn store_accessed_contract_codes(
        &self,
        key: ChunkProductionKey,
        codes: Vec<CodeBytes>,
    ) -> Result<(), Error> {
        tracing::debug!(target: "client", ?key, codes_len = codes.len(), "store_accessed_contract_codes");
        let update = CacheUpdate::AccessedContractCodes(codes);
        self.process_update(key, false, update)
    }
```

**File:** core/primitives-core/src/version.rs (L568-571)
```rust
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
```

**File:** core/primitives-core/src/version.rs (L596-598)
```rust
/// Minimum supported protocol version for the current binary
pub const MIN_SUPPORTED_PROTOCOL_VERSION: ProtocolVersion = 84;

```
