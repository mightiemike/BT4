## Finding

### Title
Stale cached `cycle-total-weight` diverges from live per-signer weights in DKG vote threshold calculation - ([File: stackslib/src/chainstate/stacks/boot/signers-voting.clar])

### Summary
`signers-voting.clar`'s `vote-for-aggregate-public-key` computes a signer's live weight from `.signers` via `get-signer-weight` (a per-index, always-current lookup), but computes the *denominator* used for the consensus threshold from `get-and-cache-total-weight`, which memoizes the total signer weight for a reward cycle the first time it is queried and never invalidates it. If the underlying `.signers` weight map for that same reward cycle is later overwritten, the numerator ("weight actually recorded per vote") and denominator ("cached total weight") no longer describe the same signer set, breaking the equality that the 70% threshold check depends on — exactly the same class of bug as the reported `totalSupply` vs. live-balance mismatch in `Oracle.1.sol`, where one side of a balance/weight computation is read live and the other from a stale snapshot.

### Finding Description
`get-and-cache-total-weight` is defined as: [1](#0-0) 

and is invoked inside `vote-for-aggregate-public-key` to derive `threshold-weight`: [2](#0-1) 

The individual signer weight for a given vote, however, is fetched live for every vote via `get-signer-weight`, which reads directly through to `.signers get-signer-by-index`: [3](#0-2) 

`.signers`'s underlying map, `cycle-signer-set`, is populated by the internal `set-signers` function, which is guarded only by `last-set-cycle == reward-cycle` — it does not prevent the same reward cycle's entry from being overwritten a second time (e.g., as a consequence of the node recomputing the reward/signer set for that cycle, such as after a burnchain reorg that changes the underlying PoX-5 stacking entries used to build the signer set): [4](#0-3) 

Because `cycle-total-weight[reward-cycle]` is cached the *first* time any signer votes in that cycle and is never recomputed afterward, if `set-signers` is invoked again for the same `reward-cycle` (overwriting the weights in `cycle-signer-set`) *after* the first vote has already triggered the cache, all subsequent per-signer weight lookups (`get-signer-weight`) reflect the *new* weight table while the consensus denominator (`cached-weight`/`threshold-weight`) still reflects the *old*, now-stale total. This breaks the invariant that `sum(live per-signer weights across all signers) == cached total weight`, exactly mirroring the reported bug where `prevTotalEth`/`postTotalEth` were computed against `totalSupply()` instead of the live underlying balance.

### Impact Explanation
If the new signer set has a materially larger total weight than the stale cached total (e.g., because a reorg changed the stacked amounts that feed `pox_5_make_signer_set`/`make_signer_set`, whose weight allocation is documented at): [5](#0-4) 

then `threshold-weight` computed from the stale (smaller) total requires less absolute weight than the actual current 70% supermajority would require. This lets an aggregate public key be approved (`vote-for-aggregate-public-key`) with less than 70% of the true current signer weight — a minority-of-the-real-set approval of the DKG key used for signer coordination. Conversely, if the new total is smaller than the stale cached total, votes that should reach 70% of the (now-smaller) true weight may fail to cross the stale (too-high) threshold, causing signer sets to disagree on whether consensus was reached and stalling/forking DKG key adoption — a temporary tip/consensus disagreement among nodes/signers depending on when each observed the cache populate relative to the reorg. This matches the "High" impact bucket: a minority-triggerable static-validation/threshold divergence with a reproducible cross-node disagreement bounded to the DKG/signer-coordination path.

### Likelihood Explanation
This requires two conditions to align: (1) at least one vote for a reward cycle occurs before the reward/signer set for that cycle is finalized/re-set, and (2) `set-signers` for that same `reward-cycle` is invoked a second time with different weights. Whether the node's internal reward-set-setting logic can actually invoke `set-signers` twice for the same `reward-cycle` (e.g., across a burnchain fork/reorg that changes the PoX-5 anchor block for the cycle) is the key open question I could not fully verify — the `set-signers` docstring says it is "Called internally by the Stacks node," but the exact call sites and their idempotency guarantees around chain reorgs were not directly inspected due to the reasoning/tool budget for this pass. This uncertainty should be resolved before treating this as a confirmed, exploitable finding.

### Recommendation
Invalidate (or delete) the `cycle-total-weight` cache entry for a `reward-cycle` whenever `set-signers` is called for that cycle, so `get-and-cache-total-weight` is forced to recompute from the live `cycle-signer-set`. Alternatively, remove the caching optimization entirely and always recompute the total weight live from `.signers get-signers reward-cycle` on every vote, guaranteeing the numerator and denominator are always drawn from the same, current signer-set snapshot.

### Proof of Concept
1. Node sets `cycle-signer-set[N]` = `signers_v1` (weights based on stacked amounts at the time cycle N's reward set was first computed).
2. A signer casts a vote in cycle N, triggering `get-and-cache-total-weight` to cache `cycle-total-weight[N] = sum(signers_v1 weights)`.
3. Due to a burnchain reorg (or any node-side re-derivation) affecting cycle N's reward set, the node calls `set-signers(N, signers_v2)`, overwriting `cycle-signer-set[N]` with different weights (allowed because the only guard is `last-set-cycle == N`, not "not yet cached"/"not yet voted").
4. Subsequent votes read live weights via `get-signer-weight` against `signers_v2`, but `threshold-weight` is still computed from the stale `cycle-total-weight[N]` cached from `signers_v1`.
5. If `sum(signers_v2 weights) > sum(signers_v1 weights)`, a coalition holding less than 70% of `signers_v2`'s true total weight can still reach `threshold-weight` (computed against the smaller stale total) and get an aggregate public key approved for cycle N.

**Confidence caveat:** I was not able to confirm, within the available tool budget, whether the stacks-node internals ever actually call `set-signers` more than once for the same `reward_cycle` in practice (this would need tracing the callers of `stackerdb-set-signer-slots`/`set-signers` from Rust, e.g. in `stackslib/src/chainstate/nakamoto/signer_set.rs` or `stacks-node`). If it is confirmed that `set-signers` is strictly called at most once per reward cycle in all code paths (including reorg handling), this analog does not apply and there is no reachable equality break here.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L70-73)
```text
(define-read-only (get-signer-weight (signer-index uint) (reward-cycle uint))
    (let ((details (unwrap! (try! (contract-call? .signers get-signer-by-index reward-cycle signer-index)) (err ERR_INVALID_SIGNER_INDEX))))
        (asserts! (is-eq (get signer details) tx-sender) (err ERR_SIGNER_INDEX_MISMATCH))
        (ok (get weight details))))
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

**File:** stackslib/src/chainstate/stacks/boot/signers.clar (L26-37)
```text
;; Called internally by the Stacks node.
;; Sets the list of signers and weights for a given reward cycle.
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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L822-855)
```rust
    pub(crate) fn pox_5_make_signer_set<I>(
        entries: &mut I,
        pox_constants: &PoxConstants,
    ) -> Result<Pox5SignerSetOutput, ChainstateError>
    where
        I: Iterator<Item = Result<RawPox5Entry, PoxEntryParsingError>>,
    {
        let mut signer_set = HashMap::new();
        let mut total_ustx_locked = 0u128;
        for entry_res in entries {
            let entry = match entry_res {
                Ok(x) => x,
                Err(PoxEntryParsingError::Skip(err_str)) => {
                    warn!(
                        "Error while iterating PoX-5 entries, impacting a single entry. Dropping entry from signer set";
                        "error" => err_str
                    );
                    continue;
                }
                Err(PoxEntryParsingError::Abort(err_str)) => {
                    error!(
                        "Abort-triggering error while iterating PoX-5 entries";
                        "error" => err_str
                    );
                    return Err(ChainstateError::PoxNoRewardCycle);
                }
            };

            total_ustx_locked += entry.amount_ustx;

            signer_set
                .entry(entry.signer_key)
                .and_modify(|existing_entry| *existing_entry += entry.amount_ustx)
                .or_insert_with(|| entry.amount_ustx);
```
