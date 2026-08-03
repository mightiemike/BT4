[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** dkg/src/chunky/common.rs (L20-47)
```rust
pub fn deserialize_chunky_transcript_and_verify(
    sender: AccountAddress,
    transcript_bytes: &[u8],
    dkg_config: &ChunkyDKGSession,
    signing_pubkeys: &[DealerPublicKey],
    epoch_state: &EpochState,
) -> anyhow::Result<ChunkyTranscriptWithHash> {
    let session_max = dkg_config.expected_max_transcript_size();
    ensure!(
        transcript_bytes.len() <= session_max,
        "[ChunkyDKG] transcript size {} exceeds max {}",
        transcript_bytes.len(),
        session_max,
    );

    // Hash the canonical BCS wire bytes once up front to avoid repeated re-serialization.
    // Safe because BCS is strictly canonical: deserialize(serialize(x)) == x byte-for-byte.
    let hash = monitor!(
        "chunky_validate_transcript_hash",
        HashValue::sha3_256_of(transcript_bytes)
    );

    // Deserialize transcript
    counters::CHUNKY_DKG_OBJECT_SIZE_BYTES
        .with_label_values(&["received_transcript"])
        .observe(transcript_bytes.len() as f64);
    let transcript: ChunkyTranscript = bcs::from_bytes(transcript_bytes)
        .map_err(|e| anyhow!("[ChunkyDKG] Unable to deserialize chunky transcript: {e}"))?;
```

**File:** dkg/src/chunky/agg_subtrx_producer.rs (L270-288)
```rust
            let received = self.received_transcripts.read();

            let mut dealer_bitmask = BitVec::with_num_bits(num_validators as u16);
            let mut indexed_hashes: Vec<(usize, HashValue)> = Vec::new();
            for addr in inner_state.contributors.iter() {
                let index = *addr_to_index
                    .get(addr)
                    .ok_or_else(|| anyhow!("contributor {} not in validator set", addr))?;
                dealer_bitmask.set(index as u16);
                let hash = received
                    .get(addr)
                    .ok_or_else(|| anyhow!("contributor {} missing stored transcript", addr))?
                    .hash();
                indexed_hashes.push((index, hash));
            }
            indexed_hashes.sort_by_key(|(idx, _)| *idx);
            let dealer_transcript_hashes: Vec<HashValue> =
                indexed_hashes.into_iter().map(|(_, h)| h).collect();
            drop(received);
```

**File:** dkg/src/chunky/missing_transcript_fetcher.rs (L31-33)
```rust
/// Fetches transcripts from a specific peer via RPC. Handles both missing and equivocated
/// transcripts (where the local copy differs from the requester's).
pub struct TranscriptFetcher {
```
