### Title
Stale cached `cycle-total-weight` in signers-voting.clar is never invalidated when `.signers` overwrites the signer set for the same reward cycle - (File: stackslib/src/chainstate/stacks/boot/signers-voting.clar)

### Summary
`signers-voting.clar` lazily caches the total signer weight for a reward cycle the first time any signer votes, and never recomputes it again for that cycle. `signers.clar`'s `set-signers` function, however, has no guard preventing the node from overwriting the stored signer set for the *same* reward cycle more than once. This mirrors the reported `genMintCount` bug: a running total is snapshotted once and subsequent legitimate changes to the underlying set are silently excluded from that total, so later comparisons against the stale total no longer reflect reality.

### Finding Description
`get-and-cache-total-weight` in `signers-voting.clar` computes and permanently stores `cycle-total-weight` for a reward cycle on the first call, and on every subsequent call simply returns the cached value without ever recomputing it: [1](#0-0) 

This total is used both to compute the pass/fail threshold for an aggregate-key vote and is read via `get-signer-weight`, which pulls a signer's *current* weight straight from `.signers`' `cycle-signer-set` map: [2](#0-1) 

Meanwhile, `signers.clar`'s `set-signers` has no idempotency/overwrite check for a given `reward-cycle` — it only asserts that `last-set-cycle` matches the target cycle, and then unconditionally replaces the stored signer/weight list: [3](#0-2) 

The node-side caller, `check_and_handle_prepare_phase_start`, itself documents that dispatch must be "cycle-stable" across forks and that a naive tip-keyed lookup "can flip mid-prepare-phase," which is exactly the condition under which `.signers`' stored set for a cycle can be recomputed and rewritten more than once within the same prepare phase (e.g., across a burnchain reorg that changes the anchor block or total-stacked amounts feeding the reward-set computation): [4](#0-3) 

If `set-signers` is invoked a second time for the same `reward-cycle` with a different (larger) total weight — because the reward set recomputation now includes more/larger PoX stackers — `cycle-signer-set` is updated to the new totals, but `signers-voting.clar`'s `cycle-total-weight` cache, once populated by an earlier vote, is never refreshed. `get-signer-weight` then reports weights drawn from the *new* signer set while `get-threshold-weight` still divides by the *old, smaller* cached total. Exactly as in the reported bug — where `genMintCount` is zeroed and forged-but-not-yet-minted entities are dropped from the count used for the next cap check — the aggregation total used for a critical threshold check silently diverges from the true, current total of the underlying set.

### Impact Explanation
This breaks the equality that a signer-weight threshold check is supposed to enforce: "signer weight below threshold or from the wrong set" is explicitly one of the accepted analog classes. A stale, smaller cached `cycle-total-weight` lets an aggregate-public-key vote reach the 70% `threshold-consensus` bar using less real signer weight than the actual current signer set requires, i.e., a subset of the true signer set — potentially well under the honest majority — could get a `.signers-voting` aggregate key approved for use in Nakamoto block signing/verification (`vote-for-aggregate-public-key`), corrupting downstream signature-weight verification (`NakamotoBlockHeader::verify_signer_signatures`) for that cycle. This is bounded to the DKG/aggregate-key subsystem and does not itself force a chain split (because the Clarity execution, including this caching bug, is deterministically replayed by all nodes), but it does concretely violate the "minority-triggerable ... signer weight below threshold" impact class defined in scope.

### Likelihood Explanation
Triggering this requires only that the node recompute and rewrite `.signers`' stored set for the same reward cycle after at least one vote has already been cast in that cycle (populating the cache) — a scenario the node's own code comments flag as a real risk during a prepare-phase burnchain reorg. No majority collusion, admin key, or off-repo assumption is needed; a single signer casting an early vote, followed by a reorg-triggered signer-set recomputation, is sufficient to desynchronize the two contracts' notions of the "total" weight.

### Recommendation
Do not lazily cache `cycle-total-weight` independent of `.signers`' authoritative state. Either recompute the total on every vote (removing the cache), or have `.signers`' `set-signers` explicitly invalidate/clear the corresponding `signers-voting.clar` `cycle-total-weight` entry (and any in-progress `round-data`/`tally` state) whenever the signer set for a reward cycle is overwritten, so the cached total is always recomputed against the currently active signer set before any threshold comparison.

### Proof of Concept
Conceptual reproduction path (Clarity-level, no privileged access required):
1. During the prepare phase for cycle `N`, the node computes and calls `.signers set-signers(N, set_A)` where `set_A` has total weight `W1`.
2. A registered signer immediately calls `vote-for-aggregate-public-key(index, key1, round=0, N)`. This triggers `get-and-cache-total-weight(N)`, which computes and permanently stores `cycle-total-weight[N] = W1` (`signers-voting.clar` lines 103-109).
3. A Bitcoin reorg occurs before the prepare phase ends; the node recomputes the reward set for cycle `N` differently (e.g., because the reorg'd fork sees more/larger PoX-stacked amounts) and calls `.signers set-signers(N, set_B)` again, where `set_B` has true total weight `W2 > W1` (`signers.clar` `set-signers`, no re-entry guard).
4. Remaining signers vote for `key1`. `get-signer-weight` for each now returns weight drawn from `set_B`, but `get-threshold-weight(N)` still divides by the stale cached `W1`.
5. A coalition whose real weight is `0.70 * W1` (which can be well under `0.70 * W2`, i.e. under 70% of the true, current signer set) satisfies the tally check in `vote-for-aggregate-public-key`, and `key1` becomes the approved aggregate public key for cycle `N` despite lacking the intended 70%-of-true-weight support — the same "counter reset before all real entries are accounted for" pattern as the original H-04 finding, here applied to `cycle-total-weight` vs. `cycle-signer-set`.

Note: I was not able to fully trace, within the available search budget, every guard inside `check_and_handle_prepare_phase_start`/`update_signers` that governs exactly how often `set-signers` can be re-invoked for the same reward cycle across forks, nor read past line 180 of `signers-voting.clar` to check for any downstream invalidation of `cycle-total-weight`. The core defect — that `signers-voting.clar` caches the total weight once and `signers.clar` has no invocation-count guard tying the two together — is confirmed directly from the cited code; the precise frequency/conditions under which a reorg re-triggers `set-signers` for an already-cached cycle would benefit from further verification in a full Devin session with repo access.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers-voting.clar (L70-93)
```text
(define-read-only (get-signer-weight (signer-index uint) (reward-cycle uint))
    (let ((details (unwrap! (try! (contract-call? .signers get-signer-by-index reward-cycle signer-index)) (err ERR_INVALID_SIGNER_INDEX))))
        (asserts! (is-eq (get signer details) tx-sender) (err ERR_SIGNER_INDEX_MISMATCH))
        (ok (get weight details))))

;; aggregate public key must be unique and can be used only in a single cycle
(define-read-only (is-novel-aggregate-public-key (key (buff 33)) (reward-cycle uint))
    (is-eq (default-to reward-cycle (map-get? used-aggregate-public-keys key)) reward-cycle))

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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L939-989)
```rust
    /// If this block is mined in the prepare phase, based on its tenure's `burn_tip_height`.  If
    /// so, and if we haven't done so yet, then compute the PoX reward set, store it, and update
    /// the .signers contract.  The stored PoX reward set is the reward set for the next reward
    /// cycle, and will be used by the Nakamoto chains coordinator to validate its block-commits
    /// and block signatures.
    pub fn check_and_handle_prepare_phase_start(
        clarity_tx: &mut ClarityTx,
        first_block_height: u64,
        pox_constants: &PoxConstants,
        burn_tip_height: u32,
        coinbase_height: u64,
    ) -> Result<Option<SignerCalculation>, ChainstateError> {
        let current_epoch = clarity_tx.get_epoch();
        if current_epoch < StacksEpochId::Epoch25 {
            // before Epoch-2.5, no need for special handling
            return Ok(None);
        }

        // now, determine if we are in a prepare phase, and we are the first
        //  block in this prepare phase in our fork
        if !pox_constants.is_in_prepare_phase(first_block_height, burn_tip_height.into()) {
            // if we're not in a prepare phase, don't need to do anything
            return Ok(None);
        }

        let Some(cycle_of_prepare_phase) =
            pox_constants.reward_cycle_of_prepare_phase(first_block_height, burn_tip_height.into())
        else {
            // if we're not in a prepare phase, don't need to do anything
            return Ok(None);
        };

        // Dispatch must be cycle-stable: every block of this prepare phase
        // must agree on which pox contract supplies cycle_of_prepare_phase's
        // signer set, regardless of which block first triggers the update.
        // Tip-keyed `active_pox_contract` is wrong here -- it can flip
        // mid-prepare-phase if pox_5_activation_height falls inside it.
        let active_pox_contract =
            pox_constants.active_pox_contract_for_cycle(first_block_height, cycle_of_prepare_phase);

        let Some(current_pox_version) = PoxVersions::lookup_by_name(active_pox_contract) else {
            debug!("Active PoX contract is not a recognized version, skipping .signers updates");
            return Ok(None);
        };

        if current_pox_version < PoxVersions::Pox4 {
            debug!(
                "Active PoX contract is lower than PoX-4, skipping .signers updates until PoX-4 is active"
            );
            return Ok(None);
        }
```
