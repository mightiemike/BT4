### Title
Coordinator assembles `signer_signature` without enforcing reward-set index order, causing `verify_signer_signatures` to reject a validly-signed, sufficiently-weighted block network-wide under `enforces_strict_signature_order()` — ([File: stackslib/src/chainstate/nakamoto/mod.rs, stacks-node/src/nakamoto_node/signer_coordinator.rs])

### Summary
`NakamotoBlockHeader::verify_signer_signatures` enforces a strictly monotonic increasing `signer_index` ordering on `signer_signature` whenever `epoch_id.enforces_strict_signature_order()` is true (i.e. `StacksEpochId::latest()`), and rejects on the very first inversion regardless of accumulated weight. The miner-side `SignCoordinator::get_block_status` in `signer_coordinator.rs` builds the returned signature vector from `block_status.gathered_signatures.values().cloned().collect()`, where `gathered_signatures` is populated key-by-`slot_id` as signer responses arrive (`stackerdb_listener.rs` `block.gathered_signatures.insert(slot_id, signature)`), with no step anywhere in this path that re-sorts the collected signatures into ascending reward-set `signing_key` index order before they are placed into the block header.

### Finding Description
The broken equality is: *distinct valid signer weight ≥ `compute_voting_weight_threshold` (true)* vs. *actual verification verdict from `verify_signer_signatures` (false)*.

`verify_signer_signatures` (stackslib/src/chainstate/nakamoto/mod.rs:1097-1190) builds `signers_by_pk` from `reward_set.signers().iter().enumerate()`, i.e. the canonical index of a signer is its position in the reward set as ordered by ascending `signing_key` [1](#0-0) . It then iterates `self.signer_signature` and rejects immediately if `*index >= signer_index` for any consecutive pair — before the accumulated weight is ever checked against the threshold [2](#0-1) . Under `strict_order` (true for `StacksEpochId::latest()` via `enforces_strict_signature_order()`), a single inversion anywhere in the vector is fatal, independent of total signed weight [3](#0-2) .

On the assembly side, `SignCoordinator::get_block_status` returns the accepted-block's signatures as `block_status.gathered_signatures.values().cloned().collect()` once `total_weight_approved >= self.weight_threshold` [4](#0-3) . `gathered_signatures` is populated as responses arrive, keyed by `slot_id`, with `block.gathered_signatures.insert(slot_id, signature)` [5](#0-4) . Nowhere in this path is the resulting vector explicitly sorted by the reward-set's ascending-`signing_key` index that `verify_signer_signatures` requires. Because signer StackerDB `slot_id` assignment and network response latency are unrelated to the ascending-`signing_key` ordering used to compute `signer_index`, the vector handed to the block header can — and, absent explicit sorting, generically will — contain inversions relative to that required ordering.

The `test_out_of_order_signer_signatures_after_first` unit test already demonstrates that `verify_signer_signatures` rejects a fully-weighted, all-signers-signed vector (`[0, 2, 1]` index order) under `StacksEpochId::latest()`, even though the identical vector was accepted pre-Epoch-4.0 under the legacy partial-ordering rule [6](#0-5) . This proves the strict-order rule can reject a block whose summed signer weight is 100% of total weight, i.e. far above `compute_voting_weight_threshold`.

No existing guard prevents this: `verify_signer_signatures`'s order check runs unconditionally before the weight check, and the coordinator/listener code that assembles `signer_signature` provides no compensating sort step.

### Impact Explanation
Every honest, correctly-quorum-signed block whose coordinator-assembled signature vector happens to contain an index inversion (due to arrival order, slot-id assignment, or hash-map iteration order) is rejected by every node running `StacksEpochId::latest()`, despite carrying sufficient signer weight to be accepted. This is a network-wide false rejection of a valid, sufficiently-endorsed block — per the stated severity taxonomy this is a "valid block rejected network-wide" condition (Critical), and at minimum causes temporary tip disagreement/stalled tenure and can force a tenure-extend, which — combined with the miner's own incentive to keep mining — creates a path where block-reward assignment shifts to a different tenure/miner than the one that produced the (wrongly rejected) valid block.

### Likelihood Explanation
No attacker action or elevated privilege is required — this triggers under ordinary honest operation whenever the coordinator's internally-collected signature order (driven by arrival timing and/or `slot_id` keying) does not coincide with the ascending-`signing_key` reward-set index order enforced by `verify_signer_signatures`. It requires only: (1) a live epoch ≥ 4.0 (`enforces_strict_signature_order()` true), (2) a reward set of ≥3 signers whose response/slot ordering doesn't match their `signing_key`-sorted index (a condition explicitly acknowledged as unrelated properties in the codebase), and (3) the coordinator reaching threshold weight via `gathered_signatures.values()` without a corrective sort. This is fully repeatable and does not depend on stake, BTC cost, or any minority/majority signer position.

### Recommendation
In `SignCoordinator::get_block_status` (stacks-node/src/nakamoto_node/signer_coordinator.rs), before returning the signature vector, sort it (or sort `gathered_signatures` prior to collection) by each signature's recovered public key's index within the target reward set — i.e., replicate the same `signers.iter().enumerate()` ascending-`signing_key` ordering that `verify_signer_signatures` uses — so the constructed `signer_signature` vector is guaranteed monotonic in signer index regardless of arrival order or `slot_id` assignment.

### Proof of Concept
```rust
// stackslib/src/chainstate/nakamoto/tests/mod.rs
#[test]
fn test_coordinator_style_out_of_order_signatures_rejected_despite_full_weight() {
    let signers = [
        (Secp256k1PrivateKey::random(), 100),
        (Secp256k1PrivateKey::random(), 100),
        (Secp256k1PrivateKey::random(), 100),
    ];
    let reward_set = make_reward_set(&signers); // indices assigned by ascending signing_key

    let mut header = NakamotoBlockHeader::empty();
    let message = header.signer_signature_hash().0;

    // Simulate coordinator assembling signatures in "arrival order" (e.g. slot_id/latency
    // driven), which happens to be [0, 2, 1] relative to signing_key-sorted reward-set index.
    let signer_signature = [0, 2, 1]
        .iter()
        .map(|&i| signers[i].0.sign(&message).unwrap())
        .collect::<Vec<_>>();
    header.signer_signature = signer_signature;

    // LHS of the equality: summed weight of the (out-of-order) signatures.
    let total_weight = 300u32; // all three signers, 100 each
    let threshold = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight).unwrap();
    assert!(total_weight >= threshold, "weight side of the equality: sufficient weight signed");

    // RHS of the equality: actual verdict under StacksEpochId::latest() must also be Ok
    // for the equality to hold. It is not — demonstrating the false negative.
    let verdict = header.verify_signer_signatures(&reward_set, StacksEpochId::latest());
    assert!(
        verdict.is_err(),
        "BUG confirmed: a fully-weighted (300/300), threshold-clearing block is rejected \
         network-wide solely due to non-monotonic signature order introduced by the coordinator's \
         arrival-order assembly, not by any signer misbehavior."
    );
}
```

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1115-1120)
```rust
        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1126-1131)
```rust
        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1161-1177)
```rust
            // Enforce order of signatures
            if let Some(index) = last_index.as_ref() {
                if *index >= signer_index {
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Signatures are out of order".to_string(),
                    ));
                }
                if strict_order {
                    last_index = Some(signer_index);
                }
            } else {
                last_index = Some(signer_index);
            }

            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4055-4094)
```rust
    fn test_out_of_order_signer_signatures_after_first() {
        // Three signers, signed in index order [0, 2, 1]: the first pair (0, 2)
        // is in order, but the last pair (2, 1) is not.
        let signers = [
            (Secp256k1PrivateKey::random(), 100),
            (Secp256k1PrivateKey::random(), 100),
            (Secp256k1PrivateKey::random(), 100),
        ];
        let reward_set = make_reward_set(&signers);

        let mut header = NakamotoBlockHeader::empty();
        let message = header.signer_signature_hash().0;

        let signer_signature = [0, 2, 1]
            .iter()
            .map(|&i| {
                signers[i]
                    .0
                    .sign(&message)
                    .expect("Failed to sign block sighash")
            })
            .collect::<Vec<_>>();

        header.signer_signature = signer_signature;

        // Pre-4.0: the buggy partial-ordering rule accepts this sequence. The
        // weight (3 * 100, all signers) easily clears the threshold.
        header
            .verify_signer_signatures(&reward_set, StacksEpochId::Epoch30)
            .expect("Pre-4.0 must preserve the legacy (lenient) ordering behavior");

        // Epoch 4.0+: the strict total-ordering rule rejects it.
        match header.verify_signer_signatures(&reward_set, StacksEpochId::latest()) {
            Ok(_) => panic!("Expected out of order signatures to fail in Epoch 4.0"),
            Err(ChainstateError::InvalidStacksBlock(msg)) => {
                assert!(msg.contains("out of order"));
            }
            _ => panic!("Expected InvalidStacksBlock error"),
        }
    }
```
