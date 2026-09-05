Confirmed: the `is-in-voting-window` private function in `signers-voting.clar` is defined but **never called** anywhere in `vote-for-aggregate-public-key`. This is the direct structural analog of the Eggs.sol bug — a guard variable/function exists (`start` in Eggs, `is-in-voting-window` here) but the state-changing entry point that should be gated by it doesn't check it.

### Title
Missing `is-in-voting-window` enforcement allows premature/out-of-window DKG votes to set the aggregate public key - ([File: stackslib/src/chainstate/stacks/boot/signers-voting.clar])

### Summary
`vote-for-aggregate-public-key` [1](#0-0)  never calls the contract's own `is-in-voting-window` helper [2](#0-1) , which is documented as the intended gate: "This vote must happen after the list of signers has been set by the node, which occurs in the first block of the prepare phase" [3](#0-2) . As written, the only checks performed are: the key not already approved for the cycle, key length, key novelty, no duplicate vote, and round increments by at most 1 [4](#0-3) . None of these enforce that `reward-cycle` matches `.signers`' `get-last-set-cycle` or that the current burn height is within the target cycle's prepare phase.

### Finding Description
`get-signer-weight` looks up the signer's weight via `.signers get-signer-by-index` for the specified `reward-cycle` and asserts `tx-sender` matches the recorded signer [5](#0-4) . If a signer's index/identity for a future reward cycle is already knowable (i.e., the future signer set has been computed/persisted, e.g., because signers are known well ahead of the prepare-phase-start block in Nakamoto, per `read_reward_set_nakamoto_of_cycle`), a signer can call `vote-for-aggregate-public-key` for that `reward-cycle` at any time before the prepare phase officially begins, well before `.signers get-last-set-cycle` would normally correspond to that cycle. Because the vote is tallied and can cross the `threshold-weight` immediately (`map-insert aggregate-public-keys reward-cycle key`) [6](#0-5) , this lets the aggregate key for a reward cycle be locked in prematurely and outside the intended voting window, before all signers can react — breaking the equality "the aggregate key approved for cycle N was voted on only during cycle N's designated voting window with a stable/known signer set."

This is structurally identical to the reported Eggs.sol issue: a `start`-like invariant (`is-in-voting-window`) is defined and clearly intended to gate a state-changing entry point, but the entry point (`leverage` / here `vote-for-aggregate-public-key`) omits the check, letting an unprivileged, minority actor (a single signer with enough weight, or several) act "before the system says it's time."

### Impact Explanation
This is a signer-set-level divergence: it could let a minority of signers with sufficient weight lock in an aggregate public key for a future reward cycle prematurely/out of the intended window, potentially fixing the DKG key before other signers can cast informed votes or before the signer set is finalized. This falls under "signer weight below threshold or from the wrong set" style class in that the check on *when* a valid vote counts is missing, which could enable a temporary tip/signing disagreement or an incorrect aggregate-key commitment bounded to a cycle's signing process (High per the rubric), rather than a chain-split by itself.

### Likelihood Explanation
Requires only that a signer with a known slot index in a future cycle's signer set calls this function before the intended prepare-phase-start block — no majority collusion, no privileged access, no node operator or other party's key needed. Whether it is exploitable at all depends on whether `.signers get-signer-by-index` / `get-last-set-cycle` for the target reward cycle actually resolve before the true prepare phase (this is not fully confirmed from the available context, since `.signers` contract logic and exact write-timing weren't in the retrieved snippets) — this is the main open uncertainty.

### Recommendation
Add `(asserts! (is-in-voting-window burn-block-height reward-cycle) (err ERR_OUT_OF_VOTING_WINDOW))` inside `vote-for-aggregate-public-key`, mirroring the `is-in-voting-window` helper that already exists but is currently dead code from the perspective of this function.

### Proof of Concept
Not independently executable from the indexed context alone (would require the `.signers` contract's exact map-write timing for `get-last-set-cycle` / `get-signer-by-index` for a future cycle, which wasn't retrieved). Conceptually: a signer calls `(contract-call? .signers-voting vote-for-aggregate-public-key signer-index key round future-reward-cycle)` for a `future-reward-cycle` whose signer set is already computed/stored but whose prepare phase has not started; since `is-in-voting-window` is never invoked, the call proceeds and can be tallied/approved early. [2](#0-1) [7](#0-6)

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L70-73)
```text
(define-read-only (get-signer-weight (signer-index uint) (reward-cycle uint))
    (let ((details (unwrap! (try! (contract-call? .signers get-signer-by-index reward-cycle signer-index)) (err ERR_INVALID_SIGNER_INDEX))))
        (asserts! (is-eq (get signer details) tx-sender) (err ERR_SIGNER_INDEX_MISMATCH))
        (ok (get weight details))))
```

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L95-98)
```text
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
