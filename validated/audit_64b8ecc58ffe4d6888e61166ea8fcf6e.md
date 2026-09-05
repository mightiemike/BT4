### Title
Missing prepare-phase/voting-window check in `vote-for-aggregate-public-key` lets a single vote poison the cached total signer weight and collapse the DKG consensus threshold to zero - (File: stackslib/src/chainstate/stacks/boot/signers-voting.clar)

### Summary
`signers-voting.clar` defines a private helper `is-in-voting-window` whose purpose is to guarantee that a vote is only accepted when (a) the signer set for the target `reward-cycle` has actually been finalized (`get-last-set-cycle` equals `reward-cycle`) and (b) the current burn height is within that cycle's prepare phase. `vote-for-aggregate-public-key`, the only public entry point for casting a DKG vote, never calls this helper. [1](#0-0) [2](#0-1) 

### Finding Description
`get-and-cache-total-weight` is a memoizing function: the **first** time it is called for a given `reward-cycle` it queries `.signers get-signers reward-cycle`, sums the weights, and permanently caches the result in `cycle-total-weight`. [3](#0-2) 

`get-threshold-weight` derives the 70%-of-total consensus bar directly from this cached value: `threshold-weight = ceil(total-weight * 70 / 100)`. [4](#0-3) 

`vote-for-aggregate-public-key` calls `get-and-cache-total-weight` unconditionally as part of its `let` bindings, before performing any of its `asserts!` checks (key not yet set, key well-formed, key novel, no duplicate vote, valid round increment). None of those checks constrain *when* (which burn height) or *for which not-yet-finalized reward cycle* the vote can be cast — the only gate that would have enforced that is `is-in-voting-window`, and it is dead code. [2](#0-1) 

Consequences of the missing check:
- Any signer who can obtain a valid `signer-index` / weight lookup for a *future* `reward-cycle` (via `get-signer-weight`, which itself performs no timing check — see below) can call `vote-for-aggregate-public-key` for that `reward-cycle` at any burn height, long before that cycle's prepare phase begins.
- If, at that early point, the underlying `.signers` contract's view of the reward-cycle's signer set is still incomplete/empty (because the set for that future cycle has not yet been finalized on-chain), `get-and-cache-total-weight` will cache `total-weight = 0` (or an otherwise-wrong partial value) for that `reward-cycle` **permanently** — the cache is never invalidated or recomputed.
- Once poisoned to `0`, `get-threshold-weight` for that cycle also collapses to `0`.
- When the real prepare-phase voting later happens, the very first vote submitted (`new-total >= threshold-weight`, i.e. `>= 0`) immediately satisfies the threshold and gets inserted into `aggregate-public-keys`, regardless of the actual 70%-of-real-signer-weight consensus rule the contract is supposed to enforce.
- This breaks the intended equality "the accepted aggregate DKG key must be backed by ≥70% of the total signer weight for that reward cycle" — a single minority signer (or even an attacker racing to make the very first out-of-window call) can force approval of an arbitrary aggregate public key with far less than the required weight.

This is a minority/unprivileged-triggerable break of the exact equality class called out in the rules: "signer weight below threshold ... " for the aggregate-key vote used by the signer boot contracts (which gate PoX/Nakamoto signer aggregation), reached purely through this repo's boot contract logic — no majority collusion, node-operator access, or external chain assumption is required.

### Impact Explanation
An attacker-controlled or premature vote that poisons `cycle-total-weight` to zero for a future reward cycle causes the DKG aggregate-public-key approval for that cycle to require **no real weight majority at all** — the very first honest or malicious vote in the correct window will be accepted as "consensus," since `new-total >= 0` is trivially true. Because the aggregate public key selected for a reward cycle is consensus-critical (it underlies signer set operations that later Nakamoto blocks/tenures depend on), an incorrect/attacker-chosen key being falsely marked "approved" can lead to signer-set disagreement across nodes about which key is authoritative for that cycle — a validation-verdict/tip disagreement that different nodes/signers can compute differently depending on whether they observed the poisoning vote and cached weight before or after it happened. This lands in the "High" impact band (minority-triggerable divergence bounded to a signer voting round / temporary tip disagreement), potentially escalating toward chain-split-adjacent behavior if enough downstream logic trusts `get-approved-aggregate-key` without re-deriving the weight independently.

### Likelihood Explanation
Likelihood is moderate-to-high: the missing check is unconditional dead code (`is-in-voting-window` is defined but has zero call sites in the contract), so the bug is always present, not conditionally triggered. The only precondition is that an entity can obtain a `signer-index`/weight lookup that succeeds for a target `reward-cycle` before that cycle's signer set is fully finalized on-chain and call the public function once — no special privilege, admin key, or majority collusion is required, only correct timing relative to when `.signers` publishes the reward-cycle's signer roster.

### Recommendation
Reinstate the intended guard: call `is-in-voting-window` (or an equivalent check requiring `reward-cycle` to equal the currently finalized/last-set cycle from `.signers`, and the current `burn-block-height` to be within that cycle's prepare phase) as an `asserts!` at the top of `vote-for-aggregate-public-key`, before `get-and-cache-total-weight` is invoked. Additionally, consider not caching `cycle-total-weight` until the voting window itself has been confirmed open, so a query made outside the window can never poison the permanent cache.

### Proof of Concept
1. Let `pox-info` define `reward-cycle-length = L`, `prepare-cycle-length = P`, and reward cycle `N`'s prepare phase begin at burn height `H`.
2. Before height `H` (i.e., before cycle `N`'s signer set has been finalized/published by `.signers`), an attacker who can produce any accepted `signer-index`/weight response for `reward-cycle = N` (e.g., via a stale or not-yet-updated `.signers` view returning few or zero entries) calls `(vote-for-aggregate-public-key signer-index key round=0 reward-cycle=N)`.
3. `get-and-cache-total-weight` is invoked for `reward-cycle=N` for the first time, computes `total = fold sum-weights signers u0` over whatever the `.signers` contract currently returns (potentially `0` signers before the set is finalized), and permanently stores `cycle-total-weight[N] = 0` (or an artificially low value) via `map-set`.
4. At height `H` (the real prepare phase for cycle `N`), a legitimate signer calls `vote-for-aggregate-public-key` for `reward-cycle=N`. `get-threshold-weight` reads the poisoned cached `total-weight = 0`, yielding `threshold-weight = 0`.
5. The single legitimate vote's `new-total >= threshold-weight` (`>= 0`) is trivially true, so `map-insert aggregate-public-keys reward-cycle key` succeeds and the aggregate key is marked "approved" for cycle `N` without any real 70%-weight consensus having been reached — breaking the invariant that `get-approved-aggregate-key` reflects a genuine majority-weighted signer decision.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L91-93)
```text
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

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L143-163)
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
```
