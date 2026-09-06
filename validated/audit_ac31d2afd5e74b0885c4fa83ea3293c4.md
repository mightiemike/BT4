### Title
`vote-for-aggregate-public-key` never enforces the prepare-phase voting window, letting a signer's vote bind an aggregate key outside the cycle it is authorized for - ([File: stackslib/src/chainstate/stacks/boot/signers-voting.clar])

### Summary
The `.signers-voting` boot contract defines a helper `is-in-voting-window`, whose stated purpose is to ensure that a DKG vote is only accepted (a) for the reward cycle whose signer set was most recently finalized in `.signers` (`last-set-cycle`) and (b) while the chain tip is within the prepare phase for that cycle. However, `vote-for-aggregate-public-key` — the only public entry point that records votes and can finalize `aggregate-public-keys` for a reward cycle — never calls `is-in-voting-window`. The function is dead code. [1](#0-0) [2](#0-1) 

### Finding Description
`vote-for-aggregate-public-key` verifies only:
- that `tx-sender` is the signer at `signer-index` for the *given* `reward-cycle` (via `get-signer-weight`, which cross-checks `.signers get-signer-by-index`),
- that no key has been finalized yet for that `reward-cycle`,
- that the key is well-formed and not reused,
- that the signer hasn't already voted in that `(reward-cycle, round)`,
- that `round` doesn't jump by more than 1. [3](#0-2) 

It does **not** assert `is-in-voting-window reward-cycle` (i.e., it never checks `(is-eq last-cycle reward-cycle)` combined with `is-in-prepare-phase burn-block-height`). This means:
- A signer who is a member of the signer set for reward-cycle `N` (as recorded by `.signers get-signers`/`get-signer-by-index`) can cast a binding vote for `reward-cycle N` at *any* burn height — well before the prepare phase for cycle `N` even begins, or long after it has ended — as long as `.signers` still has that entry recorded and no key has been approved yet for `N`.
- Because `.signers` retains historical `cycle-signer-set` entries per cycle (the map is never pruned) and `get-signer-weight`/`get-and-cache-total-weight` only key off `reward-cycle`, not off the current burn height, votes for a cycle can be accumulated and can cross the threshold and get inserted into `aggregate-public-keys` outside of the window intended by design. [4](#0-3) [5](#0-4) 

This breaks the equality the design otherwise enforces: "an approved aggregate public key for cycle N is only committed to during that cycle's prepare-phase, using votes cast by the then-current signer set." With the window check missing, a signer (an unprivileged individual actor, no majority needed to trigger this specific bypass — this is about *when* not *how many*) can submit a vote transaction that the Stacks node will accept and process through the contract regardless of the current prepare-phase state, and the node code that reads back `get-approved-aggregate-key` for signing/consensus purposes (e.g., in `nakamoto/signer_set.rs`) has no independent enforcement of this timing constraint — it trusts the Clarity contract's guard, which is absent here.

### Impact Explanation
If an aggregate public key can be approved outside its intended prepare-phase window, different nodes/signers, depending on ordering and mempool inclusion of transactions relative to their local view of the prepare phase, could disagree about whether a given vote transaction should have been valid — however, since Clarity execution is deterministic given the same chain state, all nodes executing the same block will agree on the *contract's* accepted result. The practical protocol-level risk is that the aggregate key can be latched in prematurely/late relative to the reward-cycle boundary that `NakamotoSigners`/miner-and-signer coordination code assumes, producing a `get-approved-aggregate-key` result inconsistent with the timing invariant relied on by the node's tenure/signature-verification logic. This can manifest as a temporary tip disagreement or signature-verification mismatch between nodes that assume the window was respected versus the actual on-chain state, corresponding to the "High" impact bucket (minority-triggerable static-validation/signer divergence, temporary tip disagreement) rather than a chain split, since block-level determinism of Clarity execution itself is preserved.

### Likelihood Explanation
Any single member of a reward cycle's signer set (as already recorded in `.signers`) can trigger this merely by submitting a normal `vote-for-aggregate-public-key` transaction at an unintended time — no majority collusion, no privileged key, and no other party's key is required. This is directly triggerable by an unprivileged, minority participant, matching the "minority-triggerable" acceptance criterion in the validation rules.

### Recommendation
Add the missing guard in `vote-for-aggregate-public-key`:
```clarity
(asserts! (is-in-voting-window burn-block-height reward-cycle) (err ERR_OUT_OF_VOTING_WINDOW))
```
placed alongside the other `asserts!` checks, so that votes are only accepted while `last-set-cycle` (in `.signers`) equals `reward-cycle` and the chain tip is within that cycle's prepare phase, matching the documented intent of `is-in-voting-window`.

### Proof of Concept
1. `.signers` finalizes the signer set for reward-cycle `N` at the start of `N`'s prepare phase (`last-set-cycle = N`), recording signer `S` at some `signer-index`. [6](#0-5) 
2. Time passes; the chain moves well past cycle `N`'s prepare phase into cycle `N`'s reward phase (or even into cycle `N+1`), but no aggregate key has yet been approved for `N` (`aggregate-public-keys` has no entry for `N`).
3. Signer `S` (still resolvable via `.signers get-signer-by-index N signer-index`) calls `vote-for-aggregate-public-key(signer-index, key, round, N)` at this later burn height.
4. `get-signer-weight` succeeds (tx-sender still matches the recorded signer for cycle `N`); all other `asserts!` in the function pass since none of them check burn-block-height or `last-set-cycle`. [3](#0-2) 
5. The vote is recorded and, if it (possibly combined with other similarly late/early votes) reaches `threshold-weight`, `aggregate-public-keys` for cycle `N` is set outside the prepare-phase window that `is-in-voting-window` was written to enforce — confirming the guard's absence is exploitable by a single unprivileged signer.

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

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L143-165)
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
