[1](#0-0) [2](#0-1)

### Citations

**File:** crates/sovereign-sdk/rollup-interface/src/state_machine/zk/batch_proof/input/v3.rs (L121-129)
```rust
pub struct PrevHashProof {
    /// Rightmost header in the L2 block hash merkle tree
    pub last_header: L2Header,
    /// Merkle proof for the last header in the previous sequencer commitment
    pub merkle_proof_bytes: Vec<u8>,
    /// Give the start of the previous sequencer commitment as a hint
    /// so index can be calculated
    pub prev_sequencer_commitment_start: u64,
}
```

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L517-520)
```rust
                let index = commitment
                    .l2_end_block_number
                    .checked_sub(prev_hash_proof.prev_sequencer_commitment_start)
                    .expect("Index underflow") as usize;
```
