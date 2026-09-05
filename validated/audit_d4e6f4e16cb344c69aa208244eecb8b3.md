### Title
`vote-for-aggregate-public-key` never enforces its own voting-window gate, allowing a stale/late signer vote to (re)tally toward the aggregate key of an already-closed reward cycle — (File: `stackslib/src/chainstate/stacks/boot/signers-voting.clar`)

### Summary
The external report's bug class is "no validation on an end/expiry parameter, so state that should be frozen after a deadline can still be mutated, corrupting downstream index/state calculations." In `signers-voting.clar`, a private read-only helper `is-in-voting-window` is defined specifically to gate voting to the correct prepare-phase window for a reward cycle, but it is **never called** from `vote-for-aggregate-public-key`, the only public entry point that mutates vote tallies and can set `aggregate-public-keys`.

### Finding Description
`is-in-voting-window` is defined to check that the requested `reward-cycle` equals `.signers`' `last-set-cycle` and that the current burn height is within the prepare phase: [1](#0-0) 

But `vote-for-aggregate-public-key` performs no such check — its only guards are: the aggregate key hasn't been finalized for that cycle, key length is 33 bytes, key novelty, and no duplicate vote from the same signer/round: [2](#0-1) 

Because `reward-cycle` is caller-supplied and `get-signer-weight`/`get-signers` in `.signers` retain historical signer-set data for past cycles, any signer who was part of a *past* reward cycle's signer set — one whose aggregate key vote never reached the `threshold-consensus` before the cycle ended — can still call `vote-for-aggregate-public-key` for that old `reward-cycle` at any later block height. Nothing in the contract enforces that this must happen only during that cycle's prepare-phase window (the intended "end time" of the voting period). This mirrors the Moonwell `_addEmissionConfig` bug: a config/tally that should be closed off after its `endTime`/window can still be advanced, because the validation exists in name (`ERR_OUT_OF_VOTING_WINDOW`) but the actual check backing that error is dead code.

### Impact Explanation
If enough weight accumulates on a stale vote for a past, previously-unresolved reward cycle, `map-insert aggregate-public-keys reward-cycle key` can succeed well after the cycle has closed, retroactively "electing" an aggregate public key for a cycle whose window has already passed. Any node/tooling that reads `get-approved-aggregate-key` for that reward cycle (e.g., signer coordination software, DKG completion checks, dashboards, or future contract logic gating on this map) will now see a key materialize asynchronously and non-deterministically depending on which stale votes eventually trickle in — an on-chain "wrong market index/state" analog: state for a supposedly-closed accounting period is mutated later, causing state that different observers/consumers may read differently depending on when they queried it, and enabling a signer who missed the real window to still tip a previously-unresolved consensus outcome. This is a High-severity, minority-triggerable static-validation divergence bounded to the signer-voting bookkeeping contract (no majority or admin key required — a single signer from a historical signer set with unused nonce/nonce reuse room can trigger it).

### Likelihood Explanation
Any account that appears in a `.signers` reward-cycle map (which is public data) can call this public function at any time. The only precondition is that the target `reward-cycle`'s aggregate key was never finalized during its live window (plausible whenever a DKG round fails to reach 70% consensus in time, which the contract's own round-increment logic already anticipates). This requires no special privileges, no majority coordination, and no dependence on any other party's key.

### Recommendation
Wire `is-in-voting-window` (or an equivalent height/cycle check) into `vote-for-aggregate-public-key`, asserting `(is-in-voting-window burn-block-height reward-cycle)` (or, more precisely, that `reward-cycle` is exactly `current-reward-cycle + 1` and `burn-block-height` is within that cycle's prepare phase) before accepting a vote, returning `ERR_OUT_OF_VOTING_WINDOW` otherwise so the error code's name matches actual enforced behavior.

### Proof of Concept
1. Reward cycle `N`'s signer set is set; DKG voting opens during the prepare phase preceding cycle `N`.
2. Voting fails to reach `threshold-consensus` before cycle `N` starts, so `aggregate-public-keys` for cycle `N` is never set (this is allowed — the contract has no other guard against this state persisting indefinitely, since `is-none (map-get? aggregate-public-keys reward-cycle)` remains true forever).
3. Many reward cycles later, a signer who held a slot in cycle `N`'s signer set (verifiable via `.signers get-signer-by-index`) calls `vote-for-aggregate-public-key` with `reward-cycle = N` and an old/new round number.
4. `get-signer-weight` and `get-and-cache-total-weight` still succeed (they only depend on `.signers` historical data), the vote is tallied, and if it (alone or combined with other late/replayed votes) reaches `threshold-weight`, `map-insert aggregate-public-keys N key` succeeds — finalizing a key for reward cycle `N` long after that cycle's window has closed, with no code path in the contract able to reject it on window/end-time grounds.

Note: I could not fully trace, within the available tooling, whether current stacks-signer/Nakamoto off-chain tooling additionally filters submissions to this contract by height before broadcasting (which would reduce but not eliminate exploitability, since the on-chain contract itself is the consensus-relevant enforcement point and remains unguarded). This should be verified in a full development environment.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L95-98)
```text
(define-private (is-in-voting-window (height uint) (reward-cycle uint))
    (let ((last-cycle (unwrap-panic (contract-call? .signers get-last-set-cycle))))
        (and (is-eq last-cycle reward-cycle)
            (is-in-prepare-phase height))))
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
