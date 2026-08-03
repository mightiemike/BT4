[1](#0-0) [2](#0-1)

### Citations

**File:** storage/aptosdb/src/state_value_chunk.rs (L49-65)
```rust
pub(crate) fn value_chunk_with_proof<R, F>(
    merkle_db: Arc<R>,
    version: Version,
    first_index: usize,
    chunk_size: usize,
    value_for: F,
) -> Result<StateValueChunkWithProof>
where
    R: TreeReader<StateKey> + Send + Sync + 'static,
    F: Fn(&StateKey, Version) -> Result<StateValue> + Send + Sync + 'static,
{
    let raw_values =
        jmt_leaves_with_values(Arc::clone(&merkle_db), version, first_index, value_for)?
            .take(chunk_size)
            .collect::<Result<Vec<_>>>()?;
    build_value_chunk_proof(merkle_db.as_ref(), version, first_index, raw_values)
}
```

**File:** storage/aptosdb/src/state_value_chunk.rs (L81-109)
```rust
fn build_chunk_range_proof<R, V>(
    merkle_db: &R,
    version: Version,
    first_index: usize,
    raw_values: &[(StateKey, V)],
) -> Result<ChunkRangeProof>
where
    R: TreeReader<StateKey> + Sync,
{
    if raw_values.is_empty() {
        return Err(AptosDbError::Other(format!(
            "State value chunk starting at {first_index} is empty"
        )));
    }
    let last_index = (first_index + raw_values.len() - 1) as u64;
    let first_key = raw_values.first().expect("checked non-empty").0.hash();
    let last_key = raw_values.last().expect("checked non-empty").0.hash();
    let tree = JellyfishMerkleTree::<R, StateKey>::new(merkle_db);
    let proof = tree.get_range_proof(last_key, version)?;
    let root_hash = tree.get_root_hash(version)?;
    Ok(ChunkRangeProof {
        first_index: first_index as u64,
        last_index,
        first_key,
        last_key,
        proof,
        root_hash,
    })
}
```
