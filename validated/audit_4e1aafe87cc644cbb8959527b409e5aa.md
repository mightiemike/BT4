### Title
Stale cached `cycle-total-weight` decouples the DKG vote denominator from a re-set `.signers` weight set - ([File: stackslib/src/chainstate/stacks/boot/signers-voting.clar])

### Summary
`signers-voting.clar`'s `get-and-cache-total-weight` permanently caches a reward cycle's total signer weight on the *first* vote for that cycle and never recomputes it, exactly mirroring the Panoptic bug class ("value gets locked in on first update and cannot be corrected for the remainder of the period"). The underlying `.signers` weight set for a reward cycle (`cycle-signer-set`), however, is written by `set-signers`, and the coordinator's own reward-set-caching logic documents that a reward-cycle's reward set can legitimately be recomputed/overwritten after it was first stored (when the anchor block was originally unknown and becomes known later). If `cycle-signer-set` for a reward cycle is updated a second time, the already-cached `cycle-total-weight` denominator becomes stale relative to the live weights returned by `get-signer-by-index`, breaking the equality "vote weight / total weight" that the 70% consensus threshold depends on.

### Finding Description
`get-and-cache-total-weight` in `signers-voting.clar` is a write-once cache keyed only by `reward-cycle`: [1](#0-0) 
It is invoked, unauthenticated by any special role (any registered signer can trigger it), from `vote-for-aggregate-public-key`: [2](#0-1) 
and the resulting `threshold-weight` used to decide whether an aggregate public key is approved is computed purely from that cached total: [3](#0-2) 

The per-vote weight itself, however, is read live from `.signers` via `get-signer-weight` → `get-signer-by-index` → `cycle-signer-set`, which is populated by the node-only, `define-private` function `set-signers`: [4](#0-3) [5](#0-4) 

The coordinator explicitly documents and implements a path where a reward cycle's stored reward set can be (re-)written after the fact, once an anchor block that was previously `SelectedAndUnknown` becomes known: [6](#0-5) 

If any signer casts the very first vote for a reward cycle while `cycle-signer-set` reflects an earlier/partial version of the reward set (e.g. before the anchor block was fully known, or before all signer weights were finalized), `cycle-total-weight` for that cycle gets permanently cached against that earlier weight distribution. If the node subsequently calls `set-signers` again for the same cycle with an updated (e.g., larger or differently-distributed) weight set once the anchor block is confirmed, later votes are still evaluated with `get-signer-weight` against the *new* set, but `get-and-cache-total-weight` will keep returning the *old*, uninvalidated total. This is structurally identical to `OraclePack::computeInternalMedian`'s `differentEpoch` gate: once a value is written for a given "period" key (there, the 64-second epoch; here, the reward cycle), it can never be corrected within that period, regardless of how much the underlying ground truth has since moved.

### Impact Explanation
Because `threshold-weight` is derived from a numerator (`cycle-total-weight`, fixed early) that diverges from the denominator basis actually backing individual signer weights, a minority of signer weight (measured against the *true*, updated total) could reach the 70% mark against the *stale, smaller* cached total, causing `aggregate-public-keys` to be set with an aggregate key approved by less than the intended 70% of real signing power for that reward cycle. Since the approved aggregate public key subsequently gates block signature verification for the reward cycle, this is a High-severity "signer weight below threshold" divergence bounded to the affected reward cycle's tenure — it does not require majority collusion, only a minority signer triggering the first vote at the right moment relative to the node's reward-set (re)computation.

### Likelihood Explanation
This requires the reward set for a given cycle to actually be recomputed/overwritten after the first vote already cached a total — a scenario the coordinator code (`need_to_store` logic) shows is a real, if narrow, path (unknown anchor block becoming known later). I was not able to fully verify, within the remaining budget, whether `set-signers` (the `.signers` Clarity function) is ever invoked a second time for the *same* reward cycle with a *different* weight distribution once voting has already started for that cycle, nor whether `vote-for-aggregate-public-key`'s (unused-looking) `is-in-voting-window` helper is actually enforced as a pre-condition somewhere I didn't reach — this bounds my confidence in the reachability of this path, and should be independently confirmed before treating this as fully proven.

### Recommendation
Key `cycle-total-weight` (and invalidate it) whenever `set-signers` is invoked for a given cycle, so any re-write of the signer set forces the cached total in `signers-voting.clar` to be recomputed on the next vote, rather than only ever computing it once per cycle.

### Proof of Concept
Conceptual PoC (not run, given ask-only constraints):
1. Node calls `.signers set-signers` for reward-cycle `N` with weight set `S1` (total weight `T1`), e.g. because the anchor block for `N` was initially resolved with partial stacker participation.
2. A signer immediately calls `.signers-voting vote-for-aggregate-public-key` for cycle `N`; `get-and-cache-total-weight` caches `cycle-total-weight[N] = T1`.
3. The node later determines the true/anchor-confirmed reward set for `N` is `S2` (total weight `T2 > T1`) and calls `set-signers` again for cycle `N`, overwriting `cycle-signer-set[N]`.
4. Subsequent votes are weighed via `get-signer-weight`, which reads live from `cycle-signer-set[N]` (i.e., `S2`), but `get-threshold-weight` still computes against the stale `T1`.
5. A subset of `S2` signers whose combined weight is ≥ `0.70 * T1` but < `0.70 * T2` can force approval of an aggregate public key that does not actually represent 70% of the cycle's real signing power.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L70-73)
```text
(define-read-only (get-signer-weight (signer-index uint) (reward-cycle uint))
    (let ((details (unwrap! (try! (contract-call? .signers get-signer-by-index reward-cycle signer-index)) (err ERR_INVALID_SIGNER_INDEX))))
        (asserts! (is-eq (get signer details) tx-sender) (err ERR_SIGNER_INDEX_MISMATCH))
        (ok (get weight details))))
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L91-93)
```text
(define-read-only (get-threshold-weight (reward-cycle uint))
    (let  ((total-weight (default-to u0 (map-get? cycle-total-weight reward-cycle))))
        (/ (+ (* total-weight threshold-consensus) u99) u100)))
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

**File:** stackslib/src/chainstate/stacks/boot/signers.clar (L26-33)
```text
;; Called internally by the Stacks node.
;; Sets the list of signers and weights for a given reward cycle.
(define-private (set-signers
                 (reward-cycle uint)
                 (signers (list 4000 { signer: principal, weight: uint })))
     (begin
      (asserts! (is-eq (var-get last-set-cycle) reward-cycle) (err ERR_CYCLE_NOT_SET))
      (ok (map-set cycle-signer-set reward-cycle signers))))
```

**File:** stackslib/src/chainstate/coordinator/mod.rs (L799-844)
```rust
    // cache the reward cycle info as of the first sortition in the prepare phase, so that
    // the first Nakamoto epoch can go find it later.  Subsequent Nakamoto epochs will use the
    // reward set stored to the Nakamoto chain state.
    let ic = sort_db.index_handle(sortition_tip);
    let prev_reward_cycle = burnchain
        .block_height_to_reward_cycle(burn_height)
        .expect("FATAL: no reward cycle for burn height");

    if prev_reward_cycle > 1 {
        let prepare_phase_start = burnchain
            .pox_constants
            .prepare_phase_start(burnchain.first_block_height, prev_reward_cycle - 1);
        let first_prepare_sn =
            SortitionDB::get_ancestor_snapshot(&ic, prepare_phase_start, sortition_tip)?
                .expect("FATAL: no start-of-prepare-phase sortition");

        let mut tx = sort_db.tx_begin()?;
        let preprocessed_reward_set =
            SortitionDB::get_preprocessed_reward_set(&tx, &first_prepare_sn.sortition_id)?;

        // It's possible that we haven't processed the PoX anchor block at the time we have
        // processed the burnchain block which commits to it.  In this case, the PoX anchor block
        // status would be SelectedAndUnknown.  However, it's overwhelmingly likely (and in
        // Nakamoto, _required_) that the PoX anchor block will be processed shortly thereafter.
        // When this happens, we need to _update_ the sortition DB with the newly-processed reward
        // set.  This code performs this check to determine whether or not we need to store this
        // calculated reward set.
        let need_to_store = if let Some(reward_cycle_info) = preprocessed_reward_set {
            // overwrite if we have an unknown anchor block
            !reward_cycle_info.is_reward_info_known()
        } else {
            true
        };
        if need_to_store {
            debug!(
                "Store preprocessed reward set for cycle";
                "reward_cycle" => prev_reward_cycle,
                "prepare-start sortition" => %first_prepare_sn.sortition_id,
                "reward_cycle_info" => format!("{:?}", &reward_cycle_info)
            );
            SortitionDB::store_preprocessed_reward_set(
                &mut tx,
                &first_prepare_sn.sortition_id,
                &reward_cycle_info,
            )?;
        }
```
