### Title
Zero-weight reward set collapses the Nakamoto signer-approval threshold to zero, letting a block with no signer signatures be accepted as valid - (File: stackslib/src/chainstate/nakamoto/mod.rs)

### Summary
`NakamotoBlockHeader::verify_signer_signatures` computes the minimum signing weight required to approve a block by calling `compute_voting_weight_threshold(total_weight)`, where `total_weight` comes directly from the reward set's `total_signing_weight()`. Neither function checks that `total_weight` (and therefore the resulting `threshold`) is non-zero before using it in the `total_weight_signed < threshold` comparison. This is structurally the same bug class as GHSA-fphv-w9fq-2525: a computed/derived threshold value is used for a `>=`/`<` equality check without first validating it is a positive value, so a threshold of `0` silently disables the check.

### Finding Description
`compute_voting_weight_threshold` is: [1](#0-0) 

and it is fed `total_weight` from the reward set here: [2](#0-1) 

Note the guard only rejects the case where `reward_set.signers()` is `None`: [3](#0-2) 

It does **not** reject the case where `signers()` returns `Some(vec![])` (an empty, but present, signer list) or a signer list whose entries all carry `weight == 0`. In either case `total_signing_weight()` returns `Ok(0)`, so `total_weight = 0`. Feeding `0` into `compute_voting_weight_threshold`: `(0 * 7) % 10 == 0` → `ceil = 0` → `threshold = 0`.

The final check is: [4](#0-3) 

With `threshold == 0` and an attacker-supplied `signer_signature` vector that is empty (`total_weight_signed = 0`), the condition `total_weight_signed < threshold` is `0 < 0 == false`, so the function returns `Ok(0)` — the block is treated as validly signed **with zero real signer approvals**, exactly mirroring how go-tuf's unvalidated `threshold <= 0` made `VerifyDelegate` always pass.

### Impact Explanation
`verify_signer_signatures` is the sole gate that enforces the Nakamoto supermajority signer-approval equality (the analog of a TUF delegation threshold check) for every node that processes a block. If a reward cycle's reward set ever has `total_signing_weight() == 0` (e.g., an empty/degenerate signer set for that cycle), every node deterministically computes the same `threshold = 0`, so a block with **no signer signatures at all** is uniformly accepted as validly signed network-wide. This breaks the "a signer-approved block must carry majority-weight signatures" invariant and lets a single miner author and have accepted, without any signer oversight, an otherwise-invalid block during that window — matching the "invalid block accepted... network-wide" Critical impact class, since all honest nodes agree to accept it (no split), but the acceptance criterion itself has been silently disabled.

### Likelihood Explanation
This requires no majority collusion and no signer key — any party that can get a block considered under a reward set with total signing weight `0` can supply an all-empty (or wholly-invalid) `signer_signature` and have it accepted, satisfying the "minority/unprivileged-triggerable" bar. The trigger condition (a reward set degenerating to zero total weight) is a chainstate-derived edge case rather than a routine one, which is why this is flagged as an unvalidated-threshold defect rather than a everyday exploitable path — but the code path itself performs no defensive check against it, unlike the fixed go-tuf logic which now requires `threshold >= 1`.

### Recommendation
In `compute_voting_weight_threshold` (and/or at the call site in `verify_signer_signatures`), explicitly reject `total_weight == 0` by returning `ChainstateError::NoRegisteredSigners` (or an equivalent `InvalidStacksBlock`) instead of silently producing a `threshold` of `0`, mirroring the go-tuf fix that requires the threshold to be validated as `>= 1` before use in the signature-weight comparison.

### Proof of Concept
1. Construct a `RewardSet` whose `signers` field is `Some(vec![])` (or all entries have `weight: 0`), reachable via `stackslib/src/chainstate/nakamoto/mod.rs:1122-1124`'s `reward_set.total_signing_weight()` returning `Ok(0)`.
2. Build a `NakamotoBlockHeader` with `signer_signature: vec![]` (no signatures).
3. Call `header.verify_signer_signatures(&reward_set, epoch_id)`.
4. Observe: `threshold = compute_voting_weight_threshold(0) == 0`; loop over zero signatures leaves `total_weight_signed = 0`; the check `0 < 0` is `false`, so the function returns `Ok(0)` — the block is accepted as validly signed despite having zero signer approvals, analogous to `test_exactly_enough_votes`/`test_just_not_enough_votes` in `stackslib/src/chainstate/nakamoto/tests/mod.rs:3691-3747`, which only test nonzero-weight scenarios and do not cover the `total_weight == 0` degenerate case.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1103-1107)
```rust
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1122-1124)
```rust
        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1175-1189)
```rust
            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
        }

        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1194-1207)
```rust
    pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
        let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
        let total_weight = u64::from(total_weight);
        let ceil = if (total_weight * threshold) % 10 == 0 {
            0
        } else {
            1
        };
        u32::try_from((total_weight * threshold) / 10 + ceil).map_err(|_| {
            ChainstateError::InvalidStacksBlock(
                "Overflow when computing nakamoto block approval threshold".to_string(),
            )
        })
    }
```
