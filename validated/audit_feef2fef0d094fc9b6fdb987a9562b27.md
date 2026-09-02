### Title
Non-segwit block with decoy `WITNESS_COMMITMENT_PREFIX` OP_RETURN in coinbase makes `BitcoinVerifier::verify_transactions` reject a block that honest full nodes' `calculate_witness_root`/`HeaderWrapper` accept - (File: `crates/bitcoin-da/src/verifier.rs`, `crates/bitcoin-da/src/service.rs`)

### Summary
`BitcoinVerifier::verify_transactions` and the honest node's `calculate_witness_root` (used by `BitcoinService::get_block_by_hash` to compute the `HeaderWrapper.txs_commitment` that the circuit later verifies against) use the identical `rposition`/`WITNESS_COMMITMENT_PREFIX`/`MINIMUM_WITNESS_COMMITMENT_SIZE` heuristic to decide "is this coinbase segwit-committing". That agreement is what the `test_malicious_witness_empty_witness` test exercises and expects to fail with `InvalidWitnessCommitmentStructure`. However, the honest side (`calculate_witness_root`) never dereferences `coinbase_tx.input[0].witness`, while the circuit side unconditionally does via `.ok_or(InvalidWitnessCommitmentStructure)`, so a genuinely non-segwit coinbase (no witness data at all) that merely contains a decoy OP_RETURN output starting with `0x6a24aa21a9ed` and padded to ≥38 bytes causes the honest node to compute a normal `txs_commitment` and accept the block, while the same block makes `verify_transactions` return `Err(InvalidWitnessCommitmentStructure)` in-circuit.

### Finding Description
Binding claimed: `BitcoinVerifier::verify_transactions`'s classification of block `B` (Ok/segwit-consistent vs `Err(InvalidWitnessCommitmentStructure)`) == the classification an honest full node reaches when building `HeaderWrapper` for `B` via `BitcoinService::get_block_by_hash` → `calculate_witness_root`.

Both functions independently search the coinbase's outputs for a witness-commitment-shaped output: [1](#0-0) [2](#0-1) 

They use the exact same prefix/size predicate, so for a given block they will agree on `Some(idx)` vs `None`. This is where the equality is supposed to hold. It breaks in what each side does *after* finding `Some(idx)`:

- Honest node (`calculate_witness_root`, called from `get_block_by_hash`): on `Some(idx)` it simply substitutes `Wtxid::all_zeros()` for the coinbase hash in the merkle tree and moves on — it never reads `coinbase_tx.input[0].witness`: [3](#0-2) [4](#0-3) 

- Circuit (`verify_transactions`): on `Some(commitment_idx)` it unconditionally reads the coinbase's first input's witness stack and fails hard if it's empty: [5](#0-4) 

For a truly non-segwit coinbase (no marker/flag, no witness stack at all — `Witness::new()`), `coinbase_tx.input[0].witness.iter().next()` is `None`, so line 225 returns `Err(ValidationError::InvalidWitnessCommitmentStructure)`.

Attacker's exact transaction: mine (on regtest, near-zero difficulty, no majority hashrate required) a block whose coinbase transaction has zero witness data and includes an extra output `TxOut { script_pubkey: 0x6a24aa21a9ed || <32 arbitrary bytes> }` — an OP_RETURN payload the attacker deliberately shapes to collide with `WITNESS_COMMITMENT_PREFIX` and satisfy `MINIMUM_WITNESS_COMMITMENT_SIZE`. This is fully valid per Bitcoin consensus rules: segwit is a soft fork, so a block with no witness-bearing transactions and an arbitrary OP_RETURN output is accepted by Bitcoin Core with no witness commitment requirement.

Existing safeguards do not prevent the divergence: `test_malicious_witness_empty_witness` in `bin/citrea/tests/bitcoin/bitcoin_verifier.rs` (lines 593-657) already documents and asserts that `verify_transactions` returns `Err(InvalidWitnessCommitmentStructure)` for exactly this construction — proving the in-circuit behavior — but there is no accompanying test or code path checking that the honest node's `HeaderWrapper`/`calculate_witness_root` treats the same block the same way; it demonstrably does not, since it never inspects the witness field.

### Impact Explanation
Any honest full node that fetches this block via `get_block_by_hash` computes a well-formed `txs_commitment` and accepts the block as part of its canonical DA view (feeding sequencer/prover pipelines, `get_block_at`, `get_head_block_header`, etc.). When that same block is later fed into `BitcoinVerifier::verify_transactions` (as done inside the light-client-prover / batch-prover circuit to validate DA inclusion/exclusion for the block), verification fails with `InvalidWitnessCommitmentStructure`. This makes a genuinely-mined, consensus-valid L1 block unprovable in-circuit while honest nodes have already built on top of it — matching the Critical category "a true state transition made unprovable" / "a light client proof split where two honest provers commit different outputs for the same L1 block" (since a prover attempting to process this block cannot produce a valid light client proof, while another observer treating the block only via the node-side path sees it as normal). This is repeatable at any block height by anyone able to mine or influence the coinbase of a block (trivial on regtest/devnet used for Citrea's test/CI environment referenced by this audit; on mainnet it requires successfully mining at least one block, which is possible with any nonzero hash power given enough attempts, though probabilistic).

### Likelihood Explanation
Preconditions: regtest (or any network with attacker-controllable mining, as stated in the prompt) and the ability to shape the coinbase transaction template (any solo miner/pool operator can do this; on regtest this requires no hash power at all). Cost: negligible on regtest (a single `generatetoaddress`/custom block template); on mainnet it requires actually mining a block, i.e., real proof-of-work expenditure, but no majority hashrate — a single lucky/self-mined block suffices. The construction is fully deterministic (attacker chooses the OP_RETURN bytes precisely) and repeatable across any number of blocks a miner controls.

### Recommendation
Make the honest-node classification consult the same structural requirement the circuit enforces, or relax the circuit to match the honest node: either (a) have `calculate_witness_root` (and thus `HeaderWrapper` construction in `get_block_by_hash`) also check that the coinbase's `input[0].witness` is non-empty before treating `Some(idx)` as a "segwit-style" commitment — falling back to the non-segwit path (`t.compute_wtxid()`, real merkle root) when the witness stack is empty — or (b) have `verify_transactions` treat an empty witness stack at a `Some(commitment_idx)` position the same way `test_malicious_witness_prefix_only` treats a too-short commitment output: fall through to the non-segwit branch (checking `block_header.merkle_root() == block_header.txs_commitment` and requiring `blobs.is_empty()`) instead of hard-failing with `InvalidWitnessCommitmentStructure`. Either fix must be applied symmetrically so `calculate_witness_root` and `verify_transactions` always reach the same Ok/Err classification for the same block bytes.

### Proof of Concept
```
cargo test -p bitcoin-da -- test_malicious_witness_empty_witness
```
Extend this existing test (or add a new one) to also compute `crate::service::calculate_witness_root` (or call `BitcoinService::get_block_by_hash`-equivalent construction) over the same malicious coinbase/block bytes used in `test_malicious_witness_empty_witness`, and assert:
1. `calculate_witness_root(&[malicious_coinbase], 1)` (or the multi-tx non-segwit variant with tx_count > 1) succeeds and produces a concrete `[u8;32]` commitment (honest node accepts the block, no panic/error).
2. `verifier.verify_transactions(&header_built_from_that_commitment, inclusion_proof, vec![])` returns `Err(ValidationError::InvalidWitnessCommitmentStructure)`.

The two assertions together demonstrate the binding is broken: the honest side computes a valid `txs_commitment` (implicitly classifying the block as acceptable), while `verify_transactions` given the header carrying that exact `txs_commitment` rejects the block outright.

### Citations

**File:** crates/bitcoin-da/src/verifier.rs (L194-200)
```rust
        let commitment_idx = coinbase_tx.output.iter().rposition(|output| {
            output.script_pubkey.as_bytes().len() >= MINIMUM_WITNESS_COMMITMENT_SIZE
                && output
                    .script_pubkey
                    .as_bytes()
                    .starts_with(WITNESS_COMMITMENT_PREFIX)
        });
```

**File:** crates/bitcoin-da/src/verifier.rs (L217-225)
```rust
            Some(commitment_idx) => {
                let merkle_root =
                    merkle_tree::BitcoinMerkleTree::new(inclusion_proof.wtxids).root();

                let input_witness_value = coinbase_tx.input[0]
                    .witness
                    .iter()
                    .next()
                    .ok_or(ValidationError::InvalidWitnessCommitmentStructure)?;
```

**File:** crates/bitcoin-da/src/service.rs (L1270-1278)
```rust
        let txs = block.txdata.into_iter().map(Into::into).collect::<Vec<_>>();
        let tx_count = txs.len();

        let witness_root = calculate_witness_root(&txs, tx_count);

        Ok(BitcoinBlock {
            header: HeaderWrapper::new(block.header, tx_count as u32, height, witness_root),
            txdata: txs,
        })
```

**File:** crates/bitcoin-da/src/service.rs (L1395-1426)
```rust
/// Compute the witness merkle root of txs.
fn calculate_witness_root(txdata: &[TransactionWrapper], tx_count: usize) -> [u8; 32] {
    // If there is only one transaction in the block, the witness root is all zeros
    // So the merkle root is all zeros as well
    if tx_count == 1 {
        return [0u8; 32];
    }

    let hashes = txdata
        .iter()
        .enumerate()
        .map(|(i, t)| {
            if i == 0 {
                let commitment_idx = t.output.iter().rposition(|output| {
                    output.script_pubkey.as_bytes().len() >= MINIMUM_WITNESS_COMMITMENT_SIZE
                        && output
                            .script_pubkey
                            .as_bytes()
                            .starts_with(WITNESS_COMMITMENT_PREFIX)
                });
                // If non-segwit block, the coinbase tx should also use the txid instead of all zeros
                match commitment_idx {
                    Some(_) => Wtxid::all_zeros().to_raw_hash().to_byte_array(),
                    None => t.compute_wtxid().to_raw_hash().to_byte_array(),
                }
            } else {
                t.compute_wtxid().to_raw_hash().to_byte_array()
            }
        })
        .collect();
    BitcoinMerkleTree::new(hashes).root()
}
```
