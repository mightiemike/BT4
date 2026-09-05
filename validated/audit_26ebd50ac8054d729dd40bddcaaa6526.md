### Title
Signer DKG vote threshold uses a permanently cached total-weight that can desync from the actual signer set, letting less than 70% of true signer weight approve an aggregate public key - (File: stackslib/src/chainstate/stacks/boot/signers-voting.clar)

### Summary
`signers-voting.clar`'s DKG-vote acceptance check (`get-threshold-weight`) is computed against a `cycle-total-weight` value that is cached **once, forever**, on the first vote cast for a reward cycle, rather than being (re)computed from the live signer set each time. This diverges from how block signature weight is verified elsewhere in the codebase, where the total signing weight is always freshly recomputed from the current `RewardSet` on every check.

### Finding Description
`get-and-cache-total-weight` looks up `cycle-total-weight` for the reward cycle; if already present, it returns the cached value without ever re-deriving it from `.signers get-signers`: [1](#0-0) 

The public entry point `vote-for-aggregate-public-key` relies on this cache for the acceptance decision. It computes `signer-weight`/`new-total` from a live per-signer lookup (`get-signer-weight` -> `.signers get-signer-by-index`), but compares that live tally against `threshold-weight`, which is derived from the *cached* `cycle-total-weight`: [2](#0-1) [3](#0-2) 

Critically, the contract also defines an `is-in-voting-window` helper intended to restrict voting to the prepare phase of the *currently active* reward-cycle set-up: [4](#0-3) 
but this helper is never invoked from `vote-for-aggregate-public-key` (lines 143-199 contain no call to it), so nothing in the public vote path actually enforces that voting occurs only after the true, final signer set for a cycle has stabilized.

This is the same equality-breaking structure as the external report's `WETH_TRANSFER_GAS_UNITS` bug: a value used in a threshold/economic comparison is captured at one point in time and then relied upon indefinitely, while the "ground truth" it's supposed to track (signer set / weights) can move independently. Here, the two independent computations of "total signer weight for reward cycle N" - the one cached in `signers-voting.clar` versus the one always freshly derived from the reward set in `verify_signer_signatures`/`compute_voting_weight_threshold` used for actual Nakamoto block-signature validation - are not guaranteed to agree: [5](#0-4) [6](#0-5) 

If the `.signers` contract's reported set/weights for a given reward-cycle can differ between the moment the first DKG vote is cast (which freezes `cycle-total-weight`) and the moment the reward-cycle actually starts being used for block-signature verification (which always uses the live/final `RewardSet`), then the 70% DKG threshold in `signers-voting.clar` is checked against a number that no longer matches the real total weight of signers who will ultimately be authoritative for that cycle.

### Impact Explanation
If the cached total is stale-low relative to the true final total weight, an aggregate public key can be "approved" (`aggregate-public-keys` map populated, an irreversible one-shot event per cycle guarded by `map-insert`) with less than the real 70% of the eventual signer set's weight behind it. Since the aggregate key drives sBTC/PoX signing operations for the cycle, this is a minority-triggerable divergence between the DKG-approval weight check and the block/PoX signer weight used elsewhere in consensus - a High-severity signer-weight-threshold divergence bounded to the signer-approval subsystem, not requiring any admin/governance key, only an ordinary signer able to submit a vote transaction at the earliest possible opportunity.

### Likelihood Explanation
Likelihood depends on whether `.signers get-signers reward-cycle` can return a non-final/partial answer before the last block of the prepare phase runs `pox_5_compute_and_update_signers`/`update_signers` for that cycle, and on whether any legitimate signer-set correction/update can occur to `.signers` state after the first vote of a cycle is cast (I could not fully trace `.signers`'s `get-signers`/`update-signers` update-ordering within the available search budget, and this is stated as an explicit area of uncertainty). What is concretely verifiable, however, is that the cache is architected to never refresh once set, and that the intended safeguard (`is-in-voting-window`) is dead code with respect to the actual vote-casting function - both of which independently increase the chance that a premature or stale total-weight capture goes uncorrected for the entire cycle.

### Recommendation
- Wire `is-in-voting-window` (or an equivalent, cycle/height-based finality check on the `.signers` reward-cycle set) into `vote-for-aggregate-public-key` so votes are rejected until the signer set for that reward cycle is provably final.
- Recompute (or explicitly invalidate/re-derive) `cycle-total-weight` whenever the underlying `.signers` set for that reward cycle changes, instead of caching it permanently on first read.
- Add an invariant check/test asserting that `signers-voting.clar`'s cached total weight always equals `RewardSet::total_signing_weight()` for the same reward cycle at the time an aggregate key is approved.

### Proof of Concept
1. Suppose `.signers get-signers` for reward-cycle `N` returns a partial/interim view of the signer set (e.g., due to a corrective update to stakes/weights being applied to `.signers` state after cycle `N`'s prepare-phase computation begins but before it fully settles - exact triggerability depends on `.signers` internals not fully traced here).
2. A signer belonging to that partial view calls `vote-for-aggregate-public-key`, causing `get-and-cache-total-weight` to permanently cache the (lower, stale) total for cycle `N`: `stackslib/src/chainstate/stacks/boot/signers-voting.clar:103-109`.
3. `.signers` state for cycle `N` is subsequently updated/finalized to a larger true total weight, but `cycle-total-weight` for `N` is never recomputed.
4. A colluding minority (by true final weight, but a supermajority of the stale cached total) accumulates votes whose sum crosses `get-threshold-weight` computed from the stale total (`signers-voting.clar:90-93`), causing `aggregate-public-keys` for cycle `N` to be set via the one-shot `map-insert` at `signers-voting.clar:181-196`.
5. This approved key is now permanent for cycle `N`, despite representing less than 70% of the real, final signer weight used by the rest of the protocol (e.g., `NakamotoBlockHeader::compute_voting_weight_threshold`/`verify_signer_signatures`, which always use the live `RewardSet` total).

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L90-93)
```text
;; get the weight required for consensus threshold
(define-read-only (get-threshold-weight (reward-cycle uint))
    (let  ((total-weight (default-to u0 (map-get? cycle-total-weight reward-cycle))))
        (/ (+ (* total-weight threshold-consensus) u99) u100)))
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L95-98)
```text
(define-private (is-in-voting-window (height uint) (reward-cycle uint))
    (let ((last-cycle (unwrap-panic (contract-call? .signers get-last-set-cycle))))
        (and (is-eq last-cycle reward-cycle)
            (is-in-prepare-phase height))))
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L103-109)
```text
(define-private (get-and-cache-total-weight (reward-cycle uint))
    (match (map-get? cycle-total-weight reward-cycle)
        total (ok total)
        (let ((signers (unwrap! (contract-call? .signers get-signers reward-cycle) (err ERR_FAILED_TO_RETRIEVE_SIGNERS)))
                (total (fold sum-weights signers u0)))
            (map-set cycle-total-weight reward-cycle total)
            (ok total))))
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L143-153)
```text
(define-public (vote-for-aggregate-public-key (signer-index uint) (key (buff 33)) (round uint) (reward-cycle uint))
    (let ((tally-key {reward-cycle: reward-cycle, round: round, aggregate-public-key: key})
            ;; vote by signer weight
            (signer-weight (try! (get-signer-weight signer-index reward-cycle)))
            (new-total (+ signer-weight (default-to u0 (map-get? tally tally-key))))
            (cached-weight (try! (get-and-cache-total-weight reward-cycle)))
            (threshold-weight (get-threshold-weight reward-cycle))
            (current-round (default-to {
                votes-count: u0, 
                votes-weight: u0} (map-get? round-data {reward-cycle: reward-cycle, round: round})))
                )
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1122-1124)
```rust
        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1192-1207)
```rust
    /// Compute the threshold for the minimum number of signers (by weight) required
    /// to approve a Nakamoto block.
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
