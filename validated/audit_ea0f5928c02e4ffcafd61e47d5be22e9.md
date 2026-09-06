Confirmed root cause: `vote-for-aggregate-public-key` in `signers-voting.clar` defines `is-in-voting-window` (line 95-98, checking both that the reward cycle matches `get-last-set-cycle` and that the burn height is in the prepare phase) but this helper is **never called** inside the public `vote-for-aggregate-public-key` function body (lines 143-199). The only window-related guard actually enforced is `(asserts! (is-none (map-get? aggregate-public-keys reward-cycle)) (err ERR_OUT_OF_VOTING_WINDOW))` at line 155, which merely checks that a key hasn't already been finalized for that cycle — it does not check that voting is happening during the correct prepare phase for the correct upcoming cycle.

### Title
Signers can vote for (and finalize) the aggregate public key outside the intended voting window - (File: stackslib/src/chainstate/stacks/boot/signers-voting.clar)

### Summary
`vote-for-aggregate-public-key` is documented as only usable "after the list of signers has been set by the node, which occurs in the first block of the prepare phase" [1](#0-0) , and the contract even defines a dedicated `is-in-voting-window` predicate for this purpose [2](#0-1) . However, the public entrypoint never calls it — the only asserted guard is that no key has already been recorded for the reward cycle [3](#0-2) .

### Finding Description
Because `is-in-voting-window` is dead code, a signer (or any weighted signer with `get-signer-weight` returning nonzero for a given `reward-cycle`) can call `vote-for-aggregate-public-key` for a `reward-cycle` at any burn height, not just during that cycle's prepare phase, and even before `.signers get-last-set-cycle` reflects that cycle. This lets votes accumulate — and a key be finalized via the `map-insert aggregate-public-keys` branch at line 185 — before the correct/complete signer set for that upcoming cycle is actually finalized on-chain, breaking the equality that the approved aggregate key must be derived from the exact signer set and weights that node consensus assigns for that reward cycle's prepare phase.

`get-signer-weight` does bind the weight to `.signers get-signer-by-index reward-cycle signer-index` [4](#0-3) , so the weight source itself is tied to the reward cycle's registered set. The vulnerability is about *timing*, not about forging weight: voting/finalization can occur outside the intended window (e.g., far in advance, or after the prepare phase has already elapsed and rotated), so the `aggregate-public-keys` entry for a cycle can be locked in prematurely or stale relative to when nodes/signers expect finalization to occur, and `get-and-cache-total-weight`/`cycle-total-weight` gets cached from whatever signer-set state exists at call time [5](#0-4) , which could differ from the final, canonical signer set for that cycle if voting is allowed before the set is fully settled.

### Impact Explanation
If the aggregate key can be approved outside the sanctioned prepare-phase window relative to the true "last set cycle," the recorded `aggregate-public-keys` for a reward cycle may not correspond to the actual, final signer weight distribution nodes expect, which is a minority-triggerable divergence between the on-chain approved key and what independent nodes/signers would compute as legitimate for that cycle — matching the "signer weight below threshold or from the wrong set" class in scope. This is a High-severity finding: it does not directly cause fund loss but can cause a state (approved aggregate key) that not all nodes/signers would agree was validly finalized within the intended window, risking downstream tenure/commit validation relying on a key that shouldn't yet be considered final.

### Likelihood Explanation
Exploitability requires only being a registered signer for the relevant reward cycle (no majority or admin privilege needed) and calling the public function with any `round`/`reward-cycle` — no other checks block early/late calls besides the "key already set" guard. This is directly reachable by any single weighted signer, making it minority-triggerable and straightforward to invoke.

### Recommendation
Add the missing check `(asserts! (is-in-voting-window burn-block-height reward-cycle) (err ERR_OUT_OF_VOTING_WINDOW))` inside `vote-for-aggregate-public-key`, consistent with the function's documented intent and the already-defined-but-unused helper.

### Proof of Concept
Given the current code, any signer with nonzero weight for `reward-cycle` can call:
```clarity
(contract-call? .signers-voting vote-for-aggregate-public-key signer-index key round reward-cycle)
```
at a burn height outside the prepare phase for that cycle (or before `.signers get-last-set-cycle` equals `reward-cycle`), and the call succeeds because no code path in `vote-for-aggregate-public-key` (lines 143-199) references `is-in-voting-window`. Repeating this with enough weighted signers finalizes `aggregate-public-keys` for that cycle prematurely via the `map-insert` at line 185, with no window enforcement.

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

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L153-155)
```text
                )
        ;; Check that the key has not yet been set for this reward cycle
        (asserts! (is-none (map-get? aggregate-public-keys reward-cycle)) (err ERR_OUT_OF_VOTING_WINDOW))
```
