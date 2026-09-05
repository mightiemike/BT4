[1](#0-0) [2](#0-1)

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L988-990)
```rust
        // fetch the parent tenure fees by reading the total tx fees from this block's
        // *parent* (not parent_tenure_start_header), because `parent_block_id` is the last
        // block of that tenure, so contains a total fee accumulation for the whole tenure
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L991-1003)
```rust
        let parent_tenure_fees = if parent_tenure_start_header.is_nakamoto_block() {
            Self::get_total_tenure_tx_fees_at(
                chainstate_tx,
                &block.header.parent_block_id
            )?.ok_or_else(|| {
                warn!("While processing tenure change, failed to look up parent block's total tx fees";
                      "parent_block_id" => %block.header.parent_block_id,
                      "consensus_hash" => %block.header.consensus_hash,
                      "stacks_block_hash" => %block.header.block_hash(),
                      "stacks_block_id" => %block.header.block_id()
                    );
                ChainstateError::NoSuchBlockError
            })?
```
