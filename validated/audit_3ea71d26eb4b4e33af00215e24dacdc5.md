[1](#0-0) [2](#0-1)

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3660-3670)
```rust
        // get the block-commit for this block
        let sn =
            SortitionDB::get_block_snapshot_consensus(sortdb_conn, &block.header.consensus_hash)?
                .ok_or(ChainstateError::NoSuchBlockError)
                .inspect_err(|_e| {
                    warn!("No block-commit for block";
                        "consensus_hash" => %block.header.consensus_hash,
                        "stacks_block_hash" => %block.header.block_hash(),
                        "stacks_block_id" => %block.header.block_id()
                    );
                })?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3683-3685)
```rust
        // N.B. passing block.block_id() here means that we'll look into the parent tenure
        block.validate_vrf_seed(sortdb_conn, chainstate_conn, &block_commit)
    }
```
