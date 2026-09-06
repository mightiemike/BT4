### Title
`vote-for-aggregate-public-key` never enforces the prepare-phase voting window it defines - (File: `stackslib/src/chainstate/stacks/boot/signers-voting.clar`)

### Summary
The signer boot contract defines a private guard, `is-in-voting-window`, whose stated purpose is to restrict aggregate-public-key voting to the prepare phase of the reward cycle for which the signer set was most recently finalized [1](#0-0) . The public entry point `vote-for-aggregate-public-key`, however, never calls this guard anywhere in its body, so the check can be bypassed entirely by any signer at any time.

### Finding Description
`vote-for-aggregate-public-key` is documented as only valid "after the list of signers has been set by the node, which occurs in the first block of the prepare phase" [2](#0-1) . The contract implements exactly the guard needed to enforce this: `is-in-voting-window` combines `get-last-set-cycle` (the reward cycle for which the signer set was last computed) with `is-in-prepare-phase` (a burn-height range check) [3](#0-2) .

However, the actual state-mutating function `vote-for-aggregate-public-key` only performs these `asserts!` checks: that no key is already approved for the reward cycle, that the key is 33 bytes, that the key is novel, that the signer hasn't already voted this round, and that the round increments by at most 1 [4](#0-3) . There is no call to `is-in-voting-window` (nor to `is-in-prepare-phase` directly) anywhere in the function body [5](#0-4) . This is structurally identical to the reported bug class: a state/window check exists and is clearly intended to gate a state-mutating entry point, but that entry point omits the call, so the "sale/voting is only open during X" invariant is bypassable.

Because `get-signer-weight` (called at line 146) does validate that the caller is a signer registered for `reward-cycle` via `.signers get-signer-by-index` [6](#0-5) , the caller must be an actual signer for that cycle - but nothing stops that signer from casting (or completing) votes for a `reward-cycle` outside the current prepare phase, i.e. before the signer set for that cycle is even finalized, or long after the prepare phase for that cycle has ended and a different aggregate key process has begun elsewhere. This breaks the equality the contract is meant to enforce: "an aggregate-public-key becomes approved for reward-cycle R only via votes cast during R's designated prepare-phase voting window."

### Impact Explanation
Votes accepted outside the intended prepare-phase window let a minority signer (or a small coordinated subset that individually meets `threshold-consensus`) drive `map-insert aggregate-public-keys reward-cycle key` to succeed for a cycle at an unintended burn height [7](#0-6) . Since this Clarity code executes identically and deterministically on every node, this does not by itself force a state root divergence between honest nodes - all nodes agree on the (buggy) result. The practical impact is that the aggregate signing key for a reward cycle can be committed prematurely/out-of-window, before all signer weights for that cycle are settled, potentially locking in a key that does not reflect the intended reward-cycle signer set. This can manifest as signature-verification failures for that reward cycle's Nakamoto tenures across the network (temporary tip disagreement / signer malfunction bounded to that cycle) rather than a silent chain split, since the divergence is in "when a vote is valid," not in per-node execution of identical inputs.

### Likelihood Explanation
Any legitimately-registered signer (not requiring majority or admin privilege) can trigger this merely by submitting `vote-for-aggregate-public-key` for a `reward-cycle`/`round` combination outside the current prepare phase; the current checks (`ERR_OUT_OF_VOTING_WINDOW`, novelty, duplicate-vote, round-increment) do not perform any burn-height/window validation, so nothing in the code path prevents this today.

### Recommendation
Call `(asserts! (is-in-voting-window burn-block-height reward-cycle) (err ERR_OUT_OF_VOTING_WINDOW))` inside `vote-for-aggregate-public-key`, before mutating any vote/tally state, mirroring the same window check that `is-in-voting-window` was clearly written to provide.

### Proof of Concept
1. Signer `S` is a valid signer for `reward-cycle` R per `.signers get-signer-by-index` (so `get-signer-weight` succeeds) [6](#0-5) .
2. `S` calls `vote-for-aggregate-public-key(signer-index, key, round=0, reward-cycle=R)` at a burn height that is outside R's prepare phase (e.g., well into R's reward phase, or before `.signers get-last-set-cycle` even equals R).
3. None of the `asserts!` in the function body check burn height or `get-last-set-cycle` [8](#0-7) , so the vote is tallied and, if weight is sufficient, `aggregate-public-keys` is set for R via `map-insert` at line 185, entirely outside the documented voting window.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L70-73)
```text
(define-read-only (get-signer-weight (signer-index uint) (reward-cycle uint))
    (let ((details (unwrap! (try! (contract-call? .signers get-signer-by-index reward-cycle signer-index)) (err ERR_INVALID_SIGNER_INDEX))))
        (asserts! (is-eq (get signer details) tx-sender) (err ERR_SIGNER_INDEX_MISMATCH))
        (ok (get weight details))))
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L79-98)
```text
(define-read-only (is-in-prepare-phase (height uint))
    (< (mod (+ (- height (get first-burnchain-block-height pox-info))
                (get prepare-cycle-length pox-info))
             (get reward-cycle-length pox-info)
            )
        (get prepare-cycle-length pox-info)))

;; get the aggregate public key for the given reward cycle (or none)
(define-read-only (get-approved-aggregate-key (reward-cycle uint))
    (map-get? aggregate-public-keys reward-cycle))

;; get the weight required for consensus threshold
(define-read-only (get-threshold-weight (reward-cycle uint))
    (let  ((total-weight (default-to u0 (map-get? cycle-total-weight reward-cycle))))
        (/ (+ (* total-weight threshold-consensus) u99) u100)))

(define-private (is-in-voting-window (height uint) (reward-cycle uint))
    (let ((last-cycle (unwrap-panic (contract-call? .signers get-last-set-cycle))))
        (and (is-eq last-cycle reward-cycle)
            (is-in-prepare-phase height))))
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L124-132)
```text
;; Signer vote for the aggregate public key of the next reward cycle
;;  Each signer votes for the aggregate public key for the next reward cycle.
;;  This vote must happen after the list of signers has been set by the node,
;;  which occurs in the first block of the prepare phase. The vote is concluded
;;  when the threshold of `threshold-consensus / 1000` is reached for a
;;  specific aggregate public key. The vote is weighted by the amount of
;;  reward slots that the signer controls in the next reward cycle. The vote
;;  may require multiple rounds to reach consensus, but once consensus is
;;  reached, later rounds will be ignored.
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L143-199)
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
        (ok true)))
```
