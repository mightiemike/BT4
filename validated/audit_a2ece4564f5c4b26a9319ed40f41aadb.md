[1](#0-0) [2](#0-1)

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L19-37)
```rust
/// Shadow blocks
///
/// In the event of an emergency chain halt, a SIP will be written to declare that a chain halt has
/// happened, and what transactions and blocks (if any) need to be mined at which burnchain block
/// heights to recover the chain.
///
/// If this remedy is necessary, these blocks will be mined into one or more _shadow_ blocks and
/// _shadow_ tenures.
///
/// Shadow blocks are blocks that are inserted directly into the staging blocks DB as part of a
/// schema update. They are neither mined nor relayed.  Instead, they are synthesized as part of an
/// emergency node upgrade in order to ensure that the conditions which lead to the chain stall
/// never occur.
///
/// For example, if a prepare phase is mined without a single block-commit hitting the Bitcoin
/// chain, a pair of shadow block tenures will be synthesized to create a PoX anchor block and
/// restore the chain's liveness.  As another example, if insufficiently many STX are locked in PoX
/// to get a healthy set of signers, a shadow block can be synthesized with extra `stack-stx`
/// transactions submitted from healthy stackers in order to create a suitable PoX reward set.
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L78-93)
```rust
    /// Is this a shadow block?
    ///
    /// This is a special kind of block that is directly inserted into the chainstate by means of a
    /// consensus rule.  It won't be downloaded or broadcasted, but every node will have it.  They
    /// get created as a result of a consensus-level SIP in order to restore the chain to working
    /// order.
    ///
    /// Shadow blocks have the high bit of their version field set.
    pub fn is_shadow_block(&self) -> bool {
        Self::is_shadow_block_version(self.version)
    }

    /// Is a block version a shadow block version?
    pub fn is_shadow_block_version(version: u8) -> bool {
        version & 0x80 != 0
    }
```
