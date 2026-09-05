### Title
Pre-Epoch4.0 out-of-order signer signatures accepted due to stale `last_index` in `verify_signer_signatures` - ([File: stackslib/src/chainstate/nakamoto/mod.rs])

### Summary
`NakamotoBlockHeader::verify_signer_signatures` only updates `last_index` on every iteration when `strict_order` is true (i.e. `epoch_id.enforces_strict_signature_order()`); for epochs below `Epoch40` it is set once (on the first signature) and never advanced again, so a signature sequence like reward-set indices `[0, 2, 1]` passes the order check on a pre-4.0 node even though `2 > 1` violates true reward-set enumeration order. This is confirmed as intentional, documented, epoch-gated legacy behavior, not an unbounded forgery: signatures must still come from real reward-set members and total weight must still clear the 70% threshold.

### Finding Description
The broken equality is: *"the set of signature sequences accepted by `verify_signer_signatures` as order-valid"* should equal *"permutations that are monotonically increasing in reward-set index"* — but under `strict_order == false` (pre-`Epoch40`), this equality breaks because `last_index` is captured only inside the `else` branch on the very first iteration [1](#0-0) , and subsequent comparisons check `*index >= signer_index` against that stale value instead of the true previous index when `strict_order` is false [2](#0-1) .

For the sequence `[0, 2, 1]`: iteration 1 sets `last_index = Some(0)`; iteration 2 (`index=2`) passes (`0 >= 2` false) and, since `strict_order` is false, `last_index` stays `Some(0)`; iteration 3 (`index=1`) is checked against the stale `last_index = Some(0)` (`0 >= 1` false), so it passes even though the actual previous signer index was `2`, and `2 > 1` should have been rejected under a true total-order check. This is exactly what the repo's own test `test_out_of_order_signer_signatures_after_first` documents and asserts: `Epoch30` accepts the `[0,2,1]` sequence while `StacksEpochId::latest()` (≥`Epoch40`) rejects it with "out of order" [3](#0-2) .

No existing guard closes this gap for pre-4.0 epochs: the only checks in the function are pubkey membership in `reward_set.signers()` and the accumulated `total_weight_signed` vs. `compute_voting_weight_threshold` [4](#0-3) , neither of which depends on signature ordering. Since the loose ordering is explicitly epoch-gated by design (comment: "Before Epoch 4.0, signature order check contained a bug, so gate the strict ordering behavior on the epoch"), this is intended, versioned behavior, not an unintended bypass of any invariant enforced within the same epoch context.

### Impact Explanation
Within the scope of a single epoch's rules, both sides of the equality still match: a node evaluating a block under `Epoch40`+ rules rejects `[0,2,1]`; a node evaluating the identical block under pre-`Epoch40` rules accepts it. This divergence is real but only manifests during a narrow window where two honest nodes disagree on which epoch's rules apply to the same block — i.e., a fork straddling the `Epoch40` activation boundary, or a misconfigured/stale node still running < `Epoch40` logic after the network has activated `Epoch40`. It does not by itself constitute an unbounded forgery (weight and reward-set membership are still enforced), so any accepted block still had genuine majority-weight-threshold-worth of valid, reward-set-member signatures, just possibly in the wrong order. Once all nodes are running compatible epoch logic (all ≥ or all < the boundary), there is no disagreement, since it's a deterministic function of `epoch_id` derived from chain state common to all nodes.

### Likelihood Explanation
This "bug" is explicitly epoch-gated and appears to be intentional backward-compatibility behavior preserved for pre-4.0 chain history replay/validation (per the code comment and the dedicated regression test), not a live exploitable defect against current/future epoch nodes. Triggering the divergence requires either (a) replaying/validating historical pre-4.0 blocks, where the lenient rule is the *correct* historical rule, or (b) a network genuinely split across the `Epoch40` boundary where nodes have not yet converged on epoch. In case (b), the boundary crossing is governed by burn-height thresholds common to all nodes tracking the same burnchain, so persistent disagreement beyond the standard tip-convergence window is not expected under normal operation.

### Recommendation
No code change is required for this specific behavior since it is intentionally epoch-gated and covered by an existing regression test; if the concern is that pre-4.0 validation logic could be invoked against post-4.0 chain state, ensure `epoch_id` passed into `verify_signer_signatures` is always sourced from authoritative burnchain-derived epoch determination (not attacker-influenced) at every call site, so no code path can supply a stale/lower epoch to validate a block that should be judged under strict-order rules.

### Proof of Concept
The existing test already demonstrates both sides of the equality and is sufficient evidence: `test_out_of_order_signer_signatures_after_first` in `stackslib/src/chainstate/nakamoto/tests/mod.rs:4044-4094` constructs signatures over indices `[0, 2, 1]` and asserts `verify_signer_signatures(&reward_set, StacksEpochId::Epoch30)` returns `Ok` while `verify_signer_signatures(&reward_set, StacksEpochId::latest())` returns `Err(.. "out of order" ..)` [5](#0-4) .

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1120-1120)
```rust
        let strict_order = epoch_id.enforces_strict_signature_order();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1161-1173)
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
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1189)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4044-4094)
```rust
    #[test]
    /// Test that out-of-order signatures *after the first signature* are handled
    /// according to the epoch.
    ///
    /// Before Epoch 4.0 a bug only set `last_index` on the first iteration
    /// (inside an `else` branch), so subsequent signatures were compared against
    /// the first signer's index instead of the previous one. With the sequence
    /// of indices `[0, 2, 1]`, the buggy partial-ordering rule accepts the block
    /// because index 1 > index 0 (the stale `last_index`), even though index
    /// 1 < index 2 (the actual previous signer). From Epoch 4.0 onward the
    /// strict total-ordering rule rejects it.
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
