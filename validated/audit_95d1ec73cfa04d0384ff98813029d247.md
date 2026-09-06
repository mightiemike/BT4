No vulnerability found for this question.

**Reasoning:** The premise requires two node configurations that disagree on shadow-bit handling due to a "node-local feature flag." No such flag exists in this codebase — `NakamotoBlockHeader::is_shadow_block_version` [1](#0-0)  and `expected_version_for_epoch`/`version_includes_problematic_txs` [2](#0-1)  are unconditional consensus code compiled into every node; there is no `cfg(feature = "shadow")` gate anywhere in the repo. `validate_problematic_txs` and `validate_header_static` both mask the same `0x7f` bits identically [3](#0-2) [4](#0-3) , so there is no divergence between the two functions on any single node, and there is no alternate "without shadow-block support" build variant that would handle the byte differently.

Additionally, shadow blocks are not gossiped or relayed at all — they are synthesized locally by an emergency/administrative process and inserted directly into a node's own chainstate [5](#0-4) , so the "gossip -> validate_problematic_txs" call sequence the question assumes does not apply to shadow-flagged headers in this repo. The existing test `rejects_version_epoch_mismatch` already confirms that the shadow bit is uniformly masked off and does not change acceptance semantics on any node [6](#0-5) .

Since the equality "acceptance on node A == acceptance on node B" is never broken by any code that actually exists in the repository (no feature-gated shadow logic, no gossip path for shadow blocks), this is a hypothetical scenario about code that doesn't exist rather than a reproducible bug.

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L29-40)
```rust
/// schema update. They are neither mined nor relayed.  Instead, they are synthesized as part of an
/// emergency node upgrade in order to ensure that the conditions which lead to the chain stall
/// never occur.
///
/// For example, if a prepare phase is mined without a single block-commit hitting the Bitcoin
/// chain, a pair of shadow block tenures will be synthesized to create a PoX anchor block and
/// restore the chain's liveness.  As another example, if insufficiently many STX are locked in PoX
/// to get a healthy set of signers, a shadow block can be synthesized with extra `stack-stx`
/// transactions submitted from healthy stackers in order to create a suitable PoX reward set.
///
/// This module contains shadow block-specific logic for the Nakamoto block header, Nakamoto block,
/// Nakamoto chainstate, and Nakamoto miner structures.
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L90-93)
```rust
    /// Is a block version a shadow block version?
    pub fn is_shadow_block_version(version: u8) -> bool {
        version & 0x80 != 0
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L970-990)
```rust
    pub fn version_includes_problematic_txs(version: u8) -> bool {
        // The high bit (0x80) of `version` is the shadow-block flag; the header
        // version number is the low 7 bits. Mask it off before comparing so a
        // pre-4.0 shadow block (version 0x80) isn't mistaken for a v1 header.
        (version & 0x7f) >= NAKAMOTO_BLOCK_VERSION_EPOCH_4
    }

    /// The Nakamoto block header version required for blocks in `epoch_id`.
    ///
    /// The header format (and therefore the version number, ignoring the
    /// shadow-block high bit) is fixed per epoch: Epoch 4.0+ uses
    /// [`NAKAMOTO_BLOCK_VERSION_EPOCH_4`]; earlier Nakamoto epochs use
    /// [`NAKAMOTO_BLOCK_VERSION`]. Used to reject blocks whose version does not
    /// match their epoch.
    pub fn expected_version_for_epoch(epoch_id: StacksEpochId) -> u8 {
        if epoch_id >= StacksEpochId::Epoch40 {
            NAKAMOTO_BLOCK_VERSION_EPOCH_4
        } else {
            NAKAMOTO_BLOCK_VERSION
        }
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1945-1959)
```rust
    pub fn validate_header_static(&self, epoch_id: StacksEpochId) -> bool {
        let expected_version = NakamotoBlockHeader::expected_version_for_epoch(epoch_id);
        if self.header.version & 0x7f != expected_version {
            warn!("Block has invalid header version for epoch";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id(),
                "epoch_id" => %epoch_id,
                "version" => self.header.version,
                "expected_version" => expected_version
            );
            return false;
        }
        true
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2054-2060)
```rust
        let expected_version = NakamotoBlockHeader::expected_version_for_epoch(epoch_id);
        if self.header.version & 0x7f != expected_version {
            return Err(format!(
                "invalid header version {} for epoch {epoch_id}; expected {expected_version} (shadow bit ignored)",
                self.header.version
            ));
        }
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L3430-3479)
```rust
    #[test]
    fn rejects_version_epoch_mismatch() {
        // A version-0 header in Epoch 4.0 is invalid: the markers would not be
        // committed to the block hash.
        let mut block = make_block(2);
        block.header.version = 0;
        assert!(block
            .validate_problematic_txs(StacksEpochId::Epoch40)
            .is_err());

        // A version-1 header before Epoch 4.0 is invalid even with no markers.
        let mut block = make_block(2);
        block.header.version = NAKAMOTO_BLOCK_VERSION_EPOCH_4;
        block.header.problematic_txs.clear();
        assert!(block
            .validate_problematic_txs(StacksEpochId::Epoch34)
            .is_err());

        // The shadow-block flag (high bit) does not change the version's epoch
        // classification: a shadow v1 header is valid in Epoch 4.0...
        let mut block = make_block(2);
        block.header.version = NAKAMOTO_BLOCK_VERSION_EPOCH_4 | 0x80;
        block.header.problematic_txs.clear();
        block
            .validate_problematic_txs(StacksEpochId::Epoch40)
            .unwrap();

        // ...and a shadow v0 header is valid before Epoch 4.0.
        let mut block = make_block(2);
        block.header.version = 0x80;
        block.header.problematic_txs.clear();
        block
            .validate_problematic_txs(StacksEpochId::Epoch34)
            .unwrap();

        // The version must match the epoch *exactly* (masking the shadow bit):
        // an unrecognized version is rejected in either epoch, even with no
        // markers.
        for bad_version in [2u8, 5u8, 0x82u8] {
            let mut block = make_block(2);
            block.header.version = bad_version;
            block.header.problematic_txs.clear();
            assert!(block
                .validate_problematic_txs(StacksEpochId::Epoch34)
                .is_err());
            assert!(block
                .validate_problematic_txs(StacksEpochId::Epoch40)
                .is_err());
        }
    }
```
