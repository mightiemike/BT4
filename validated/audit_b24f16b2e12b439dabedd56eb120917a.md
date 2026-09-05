No vulnerability found for this question.

The `sub` function targeted in `stackslib/src/chainstate/burn/atc.rs` is a checked-subtraction helper method on `AtcRational`, a fixed-point numeric type used for the sortition "null miner advantage" (ATC) probability calculation [1](#0-0) . It has no relationship whatsoever to `get_nakamoto_tenure_length`, which lives in a completely different module, `stackslib/src/chainstate/nakamoto/tenure.rs`, and governs Nakamoto tenure-length bounds checking [2](#0-1) .

There is no call path from any attacker-controlled input (block-commit, leader-key, Nakamoto block/microblock, poison report, or fork) into `AtcRational::sub`, nor does `AtcRational::sub` participate in tenure-length validation. The premise of the question — that `sub` in `atc.rs` is somehow invoked by `get_nakamoto_tenure_length` to allow tenure over-extension — does not correspond to any real code relationship in this repository. Since the named function and the alleged vulnerable path are unconnected, the equality/invariant described in the question cannot be traced or broken through this file/function.

### Citations

**File:** stackslib/src/chainstate/burn/atc.rs (L81-87)
```rust
    /// Checked subtraction
    pub fn sub(&self, other: &AtcRational) -> Option<Self> {
        if self.0 < other.0 {
            return None;
        }
        Some(AtcRational(self.0 - other.0))
    }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L1-1)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
```
