### Title
Flipping the shadow-block version bit lets an attacker force `verify_signer_signatures` to report full reward-set weight for an unsigned block - ([File: stackslib/src/chainstate/nakamoto/shadow.rs])

### Summary
`NakamotoBlockHeader::is_shadow_block()` classifies any block as a "shadow block" purely by testing the high bit (`0x80`) of the unauthenticated `version` byte, and `get_shadow_signer_weight()` unconditionally returns the sum of every signer's weight in the reward set, ignoring the block's actual `signer_signature` field entirely. Because the staging-DB write path (`store_block`) forces `obtain_method` to `Shadow` for any block that merely has this bit set — without first checking that the block's tenure is a pre-established shadow tenure — an attacker who controls the raw bytes of a normal, unsigned/garbage-signed `NakamotoBlock` can flip `version |= 0x80` and have it treated as if it carries full signer authorization.

### Finding Description
The intended invariant is:

`distinct_valid_signer_weight_from_real_signatures(block) >= threshold` ⇔ `verify_signer_signatures` accepts the block (SIGNING gate).

For shadow blocks this equality is broken by construction. `is_shadow_block()` / `is_shadow_block_version()` derive shadow-ness solely from an unauthenticated header byte: [1](#0-0) 

`get_shadow_signer_weight()` then reports the **entire** reward set's weight regardless of what (if anything) is in `signer_signature`: [2](#0-1) 

The staging-DB insertion logic (`store_block`), which is on the normal p2p/download/push acceptance path for *all* incoming Nakamoto blocks (not just legitimately-synthesized shadow blocks), auto-promotes any block with the high version bit set to `NakamotoBlockObtainMethod::Shadow`, and only rejects the *opposite* mismatch (a non-shadow block landing in an already-shadow tenure). It contains **no check that a shadow-flagged block belongs to a tenure that was legitimately declared a shadow tenure by the trusted tooling path** (`add_shadow_block` / `process_shadow_block` in the same file, which are explicitly marked "DO NOT RUN ON A RUNNING NODE"): [3](#0-2) 

The only place that enforces "this tenure must be empty, or already a shadow tenure" is `add_shadow_block`, which is the manual/offline tooling entry point, not the code path reached when an attacker relays a crafted block over the network: [4](#0-3) 

Consequently, a block that is otherwise a normal, real block (or one entirely fabricated by the attacker with an empty/garbage `signer_signature` vector) has only to set the top bit of `header.version`. Anywhere `verify_signer_signatures` (or logic that dispatches on `is_shadow_block()`) computes signing weight, it will report full reward-set weight via `get_shadow_signer_weight`, which is always `>= threshold`, satisfying the SIGNING gate with **zero real signer authorization**.

### Impact Explanation
If this path is reachable from attacker-controlled, unauthenticated block bytes (i.e., there is no additional gate — such as a check that the tenure/consensus_hash was already pre-registered via the trusted, operator-only shadow-repair tooling — placed strictly *before* `verify_signer_signatures` is invoked on the normal ingest path), then any single, unprivileged participant who can submit or relay a `NakamotoBlock` could get an arbitrary, unsigned block accepted as fully signed network-wide. That is a Critical-class outcome: an invalid block accepted by the network without the required signer weight, undermining the entire signer-based finality/SIGNING guarantee, potentially enabling arbitrary chain history to be inserted.

### Likelihood Explanation
The precondition is minimal: the attacker needs only the ability to submit/relay a `NakamotoBlock`'s raw bytes (a capability explicitly granted to the modeled attacker) and to set one bit (`version |= 0x80`). No signer key, no majority stake, and no privileged role is required based on the code inspected. The exploit is fully repeatable for any tenure/block the attacker can construct or intercept.

### Recommendation
- `is_shadow_block()`/`get_shadow_signer_weight()` must never be treated as authoritative unless the block's tenure has independently been confirmed to be a legitimate, consensus-activated shadow tenure (e.g., verified against a hard-coded/consensus-enforced SIP activation record), not merely by inspecting an attacker-controlled version byte.
- `store_block` should reject any newly-arriving (non-locally-synthesized) block that sets the shadow bit unless it arrives via the trusted, explicit `add_shadow_block`/`process_shadow_block` tooling entry points — i.e., add the missing symmetric check: reject a shadow-flagged block from being inserted into a tenure that is not already an established shadow tenure.
- `verify_signer_signatures` should not delegate to `get_shadow_signer_weight` based solely on the header bit; it should require corroborating proof (e.g., a chainstate-schema-level marker, not network-supplied data) that the block was inserted through the authorized shadow-block issuance mechanism.

### Proof of Concept
Rust integration test plan (chainstate harness, e.g. modeled on `stackslib/src/chainstate/nakamoto/tests/mod.rs`):
1. Build/mine a normal Nakamoto tenure and a normal, otherwise-valid block `B` with a legitimate `reward_set` of total weight `W` and threshold `T` (`T <= W`).
2. Corrupt `B.header.version |= 0x80` and truncate/garbage-fill `B.signer_signature` (do not sign with any real signer key).
3. Assert LHS: `distinct_valid_signer_weight_from_real_signatures(B) == 0` (no real signatures exist).
4. Call `NakamotoChainState::verify_signer_signatures(..., &B, &reward_set)` (or the equivalent public/pub(crate) entry) and assert RHS: it returns `Ok(W)` (or `>= T`), demonstrating the two sides diverge (`0 != W`).
5. Feed `B` through the normal ingestion path (`store_block_if_better` / `accept_block` / `process_new_nakamoto_block`) as if pushed over p2p, and assert it is stored with `obtain_method == Shadow` and `signing_weight >= T`, and is ultimately processed/accepted as canonical — despite zero real signer authorization.
6. Contrast with feeding the same tenure/consensus_hash through the legitimate `add_shadow_block` path first; show that path enforces the "tenure must be empty or already shadow" invariant that the network-ingest path lacks.

Note: I was unable to fully read `NakamotoChainState::verify_signer_signatures`'s exact call sites and the full `accept_block`/`process_new_nakamoto_block` dispatch logic in `stackslib/src/chainstate/nakamoto/mod.rs` within the available tool budget, so I cannot confirm with 100% certainty that no additional gate exists between block ingestion and `verify_signer_signatures` on the network path. This should be verified directly in that file before treating the finding as fully confirmed; a Devin session with full repo access is recommended to trace this precisely and finalize the PoC.

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L86-93)
```rust
    pub fn is_shadow_block(&self) -> bool {
        Self::is_shadow_block_version(self.version)
    }

    /// Is a block version a shadow block version?
    pub fn is_shadow_block_version(version: u8) -> bool {
        version & 0x80 != 0
    }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L95-107)
```rust
    /// Get the signing weight of a shadow block
    pub fn get_shadow_signer_weight(&self, reward_set: &RewardSet) -> Result<u32, Error> {
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };
        let shadow_weight = signers
            .iter()
            .fold(0u32, |acc, signer| acc.saturating_add(signer.weight));

        Ok(shadow_weight)
    }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L817-848)
```rust
impl NakamotoStagingBlocksTx<'_> {
    /// Add a shadow block.
    /// Fails if there are any non-shadow blocks present in the tenure.
    pub fn add_shadow_block(&self, shadow_block: &NakamotoBlock) -> Result<(), ChainstateError> {
        if !shadow_block.is_shadow_block() {
            return Err(ChainstateError::InvalidStacksBlock(
                "Not a shadow block".into(),
            ));
        }
        let block_id = shadow_block.block_id();

        // is this block stored already?
        let qry = "SELECT 1 FROM nakamoto_staging_blocks WHERE index_block_hash = ?1";
        let args = params![block_id];
        let present: Option<i64> = query_row(self, qry, args)?;
        if present.is_some() {
            return Ok(());
        }

        // this tenure must be empty, or it must be a shadow tenure
        let qry = "SELECT 1 FROM nakamoto_staging_blocks WHERE consensus_hash = ?1";
        let args = rusqlite::params![&shadow_block.header.consensus_hash];
        let present: Option<u32> = query_row(self, qry, args)?;
        if present.is_some()
            && !self
                .conn()
                .is_shadow_tenure(&shadow_block.header.consensus_hash)?
        {
            return Err(ChainstateError::InvalidStacksBlock(
                "Shadow block cannot be inserted into non-empty non-shadow tenure".into(),
            ));
        }
```

**File:** stackslib/src/chainstate/nakamoto/staging_blocks.rs (L681-692)
```rust
        let obtain_method = if block.is_shadow_block() {
            // override
            NakamotoBlockObtainMethod::Shadow
        } else {
            obtain_method
        };

        if self.conn().is_shadow_tenure(&block.header.consensus_hash)? && !block.is_shadow_block() {
            return Err(ChainstateError::InvalidStacksBlock(
                "Tried to insert a non-shadow block into a shadow tenure".into(),
            ));
        }
```
