### Title
Cached `cycle-total-weight` in `signers-voting.clar` can be poisoned by an earlier reward cycle's stale entry, causing the DKG vote threshold to diverge from the true signer weight - (File: stackslib/src/chainstate/stacks/boot/signers-voting.clar)

### Summary
`get-and-cache-total-weight` in the pox-4 `signers-voting` boot contract caches the total signer weight for a reward cycle the first time it's read, and every subsequent `vote-for-aggregate-public-key` call for that cycle reuses the cached value forever — the cache is never invalidated or refreshed. This mirrors the external report's bug class: a "should be reset on refresh" variable (`_lgeTimeStamp`) that instead survives across the operation it's supposed to be scoped to, corrupting a downstream equality check.

### Finding Description
`get-and-cache-total-weight` reads `cycle-total-weight` for `reward-cycle`; if present it returns the cached total unconditionally, otherwise it computes the total from `.signers get-signers reward-cycle` and writes it to the map: [1](#0-0) 

That cached total feeds directly into `get-threshold-weight`, which is the equality gate deciding whether a candidate aggregate public key has reached DKG consensus: [2](#0-1) 

and into `vote-for-aggregate-public-key`, where `cached-weight`/`threshold-weight` gate the `(>= new-total threshold-weight)` approval: [3](#0-2) 

The underlying `.signers` contract records the true weight set per cycle via `set-signers`, keyed only by `reward-cycle`, guarded by `last-set-cycle`: [4](#0-3) 

Because `cycle-signer-set` and `cycle-total-weight` are both keyed by the bare `reward-cycle` `uint` (not by a fork/anchor-block identifier or a "last-set-height"), any call path that causes `set-signers` to run twice for the same `reward-cycle` value (e.g., in the event of the same numeric cycle being reused, a corrected/-re-run signer computation, or a re-orchestrated prepare-phase retry that re-derives a different signer weight distribution before the first vote's cache write, versus after) leaves `signers-voting`'s cache holding the *first* computed total forever — the code has no mechanism analogous to "refresh `_lgeTimeStamp` to zero" to invalidate `cycle-total-weight` when the signer set is recomputed. Once even a single vote is cast, the cache is set and can never be recalculated for that cycle, exactly the "should be reset so it gets reassigned on refresh" defect from the report.

### Impact Explanation
If `cycle-total-weight` is cached against a stale/incorrect signer weight total (whether from a corrected signer set or any code path that recomputes `cycle-signer-set` for a cycle after the vote flow already cached the old total), `get-threshold-weight` computes a threshold based on the wrong denominator. This can let a DKG round approve an aggregate public key with less than the true 70% weight support (`threshold-weight` too low), or block a legitimate 70%-weight consensus (`threshold-weight` too high) — a signer-weight-vs-threshold divergence that is exactly the class of "signer weight below threshold or from the wrong set" equality break called out as in-scope. This can produce a chain-wide disagreement on the accepted aggregate public key for a reward cycle, a High-severity signer/tally divergence with no majority collusion required — a single caller invoking `vote-for-aggregate-public-key` early (before/after a signer-set recomputation) determines which stale total gets locked in.

### Likelihood Explanation
Triggering this requires only an ordinary, unprivileged signer call to `vote-for-aggregate-public-key` at a moment when the cached total no longer matches the live `.signers` weight table for that cycle — no majority or admin key is needed to *trigger* the caching itself, since any single vote call is what populates (and permanently freezes) the cache. Whether `set-signers`/`get-signers` can actually be invoked more than once for the same `reward-cycle` before the DKG round concludes is the load-bearing precondition I could not fully verify from the available index; the `.signers` contract's `set-signers` guard (`asserts! (is-eq (var-get last-set-cycle) reward-cycle))`) only checks that the most-recently-set cycle number matches, which does not by itself prevent a second `set-signers` call for the same cycle with a different signer list if the node driver invokes it twice (e.g. on a reorg/retry of the prepare-phase signer computation).

### Recommendation
Invalidate (or key) `cycle-total-weight` on the same event that would cause `.signers`'s `cycle-signer-set` for that cycle to be (re)written, e.g. by clearing/overwriting `cycle-total-weight` whenever `set-signers` runs for a cycle, or by keying the cache on a signer-set fingerprint/height rather than the bare `reward-cycle`, so `get-and-cache-total-weight` cannot serve a total computed against a superseded signer list.

### Proof of Concept
Conceptual repro (not verified end-to-end due to index limits on the node-side driver of `set-signers`/`stackerdb-set-signer-slots`):
1. At the start of reward cycle `N`'s prepare phase, `.signers`'s `set-signers N signers_A` is committed with total weight `W_A`.
2. A signer calls `vote-for-aggregate-public-key` once; `get-and-cache-total-weight` computes and caches `cycle-total-weight[N] = W_A`.
3. Before the DKG round concludes, the node driver recomputes and re-commits `set-signers N signers_B` with a different weight distribution/total `W_B` (e.g. due to a retried/corrected signer computation for the same numeric cycle).
4. Subsequent votes in cycle `N` still read `cycle-total-weight[N] = W_A` via `get-threshold-weight`, so the consensus threshold is computed against the stale `signers_A` total rather than the live `signers_B` total, allowing (or blocking) approval of `aggregate-public-keys[N]` at the wrong weight threshold.

I was unable to fully confirm, from the indexed code alone, whether the Nakamoto node driver ever actually calls `set-signers` twice for the same numeric `reward-cycle`; verifying that precondition (in `stackslib/src/chainstate/nakamoto` reward-set-computation code, not covered in the index) would require a Devin session with full repository access.

### Citations

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

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L143-198)
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
        ;; Check that the key has not yet been set for this reward cycle
        (asserts! (is-none (map-get? aggregate-public-keys reward-cycle)) (err ERR_OUT_OF_VOTING_WINDOW))
        ;; Check that the aggregate public key is the correct length
        (asserts! (is-eq (len key) u33) (err ERR_ILL_FORMED_AGGREGATE_PUBLIC_KEY))
        ;; Check that aggregate public key has not been used in a previous reward cycle
        (asserts! (is-novel-aggregate-public-key key reward-cycle) (err ERR_DUPLICATE_AGGREGATE_PUBLIC_KEY))
        ;; Check that signer hasn't voted in this reward-cycle & round
        (asserts! (map-insert votes {reward-cycle: reward-cycle, round: round, signer: tx-sender} {aggregate-public-key: key, signer-weight: signer-weight}) (err ERR_DUPLICATE_VOTE))
        ;; Check that the round is incremented by at most 1
        (try! (update-last-round reward-cycle round))
        ;; Update the tally for this aggregate public key candidate
        (map-set tally tally-key new-total)
        ;; Update the current round data
        (map-set round-data {reward-cycle: reward-cycle, round: round} {
            votes-count: (+ (get votes-count current-round) u1),
            votes-weight: (+ (get votes-weight current-round) signer-weight)})
        ;; Update used aggregate public keys
        (map-set used-aggregate-public-keys key reward-cycle)
        (print {
            event: "voted",
            signer: tx-sender,
            reward-cycle: reward-cycle,
            round: round,
            key: key,
            new-total: new-total,
        })
        ;; If the new total weight is greater than or equal to the threshold consensus
        (if (>= new-total threshold-weight)
            ;; Save this approved aggregate public key for this reward cycle.
            ;; If there is not already a key for this cycle, the insert will
            ;; return true and an event will be created.
            (if (map-insert aggregate-public-keys reward-cycle key)
                (begin
                    ;; Create an event for the approved aggregate public key
                    (print {
                        event: "approved-aggregate-public-key",
                        reward-cycle: reward-cycle,
                        round: round,
                        key: key,
                    })
                    true)
                false
            )
            false
        )
```

**File:** stackslib/src/chainstate/stacks/boot/signers.clar (L28-37)
```text
(define-private (set-signers
                 (reward-cycle uint)
                 (signers (list 4000 { signer: principal, weight: uint })))
     (begin
      (asserts! (is-eq (var-get last-set-cycle) reward-cycle) (err ERR_CYCLE_NOT_SET))
      (ok (map-set cycle-signer-set reward-cycle signers))))

;; Get the list of signers and weights for a given reward cycle.
(define-read-only (get-signers (cycle uint))
     (map-get? cycle-signer-set cycle))
```
