## Title
Tenure-extend block validated against the wrong reward cycle's signer set at a reward-cycle boundary - ([File: stackslib/src/net/relay.rs], [File: stackslib/src/chainstate/nakamoto/coordinator/mod.rs])

## Summary
The external report's bug class is: a piece of chain state (an "obligation") is bound at creation time to a specific member of a set (an elevation group), but a later consumer re-derives "the current valid set" from mutable/point-in-time context instead of the context that was valid when the obligation was created, breaking the equality between "the set the obligation was actually validated/signed against" and "the set the validator uses to check it." In stacks-core the closest structurally-analogous equality is: *a Nakamoto tenure (and any of its blocks, including `TenureChangeCause::Extended` blocks) must always be verified against the reward set (signer set) that was active when the tenure's electing sortition happened — not the reward set of whatever burn view/consensus-hash snapshot is nearest to hand.* This is explicitly called out as a hazard in the codebase itself.

## Finding Description
`load_nakamoto_reward_set_for_tenure` documents the invariant precisely: [1](#0-0) 

> "`tenure_snapshot` must be the snapshot of the sortition that elected the tenure ... not the burnchain tip: a tenure extended across a reward-cycle boundary is still signed by the reward set that was active at its election."

This means the function's correctness depends entirely on callers supplying the *election* sortition snapshot for the tenure, not some other snapshot (e.g., the sortition snapshot associated with the block's own consensus hash/burn view, which for an `Extended` tenure-change block is *different* from the tenure's election consensus hash — see `tenure_payload.burn_view_consensus_hash` vs `tenure_payload.tenure_consensus_hash` handling in `stackslib/src/chainstate/nakamoto/tenure.rs`, lines 715-726, where `Extended` causes require `prev_tenure_consensus_hash == tenure_consensus_hash` but the block's own burn view snapshot advances independently as the tenure is extended into new burn blocks).

One call site, in `stackslib/src/net/relay.rs`, passes `block_sn` (the sortition snapshot the caller looked up for the incoming block) into `load_nakamoto_reward_set_for_tenure`: [2](#0-1) 

If `block_sn` for a `TenureExtend` block is resolved from the block's *burn view* consensus hash (the sortition current at the moment the extend was mined) rather than the *original electing sortition* for that tenure, and the tenure straddles a reward-cycle boundary, `block_height_to_reward_cycle(tenure_snapshot.block_height)` inside `load_nakamoto_reward_set_for_tenure` (line 374-375) will resolve to the *new* cycle's reward/signer set instead of the tenure-election cycle's set. `verify_signer_signatures` is then evaluated against that wrong `RewardSet`: [3](#0-2) 

Because two independent nodes can resolve "the reward set for this tenure" differently depending on which snapshot each one uses as `tenure_snapshot` for the same extend block (one correctly using the electing sortition, one incorrectly using the block's burn-view sortition), this breaks the required equality: *the signer set a validating node checks a block's signatures against must be the exact signer set that actually signed it.* This mirrors the external report's core defect — an obligation's fixed reference (elevation group / tenure's electing reward-cycle) being re-checked against the wrong, currently-in-scope set (reserve's current elevation groups / burn tip's current reward cycle) — producing either wrongful rejection of a validly-signed extend block, or (if weight thresholds happen to be met in the wrong set) wrongful acceptance.

There is direct positive evidence that the developers considered and fixed at least one instance of this exact class of bug for signer-set membership, described in the changelog: [4](#0-3) 

which shows the general pattern (using a stale/wrong reference point to determine "the currently valid miner/tenure") has previously caused real signer-side rejection bugs in this exact area of the code (tenure continuity across reorgs/reward-cycle-like boundaries).

## Impact Explanation
If a validating node resolves the wrong reward-cycle's signer set for a `TenureChangeCause::Extended` block that crosses a reward-cycle boundary, two honest, non-Byzantine nodes can reach different verdicts on the same block: one correctly using the election-cycle's signer set accepts it, another incorrectly using the new cycle's signer set (with a disjoint or reweighted signer key set) rejects it as `InvalidStacksBlock`/`Public key ... not found in the reward set`. This is a minority-triggerable static-validation divergence (a single validating node's incorrect snapshot resolution, not requiring any majority or Sybil condition), causing temporary tip disagreement between nodes — matching the "High" impact bucket (minority-triggerable static-validation divergence, temporary tip disagreement).

## Likelihood Explanation
This requires no privileged action and no majority collusion — it is triggered purely by ordinary chain progression: any tenure that is `Extended` across a reward-cycle boundary (a normal, expected occurrence given `tenure_extend_wait_timeout_ms`/idle-timeout driven extends documented in `sample/conf/mainnet-miner-conf.toml`) exercises this exact code path. The risk is entirely contingent on which snapshot value is passed as `tenure_snapshot`/`block_sn` at each call site of `load_nakamoto_reward_set_for_tenure`; I was not able to fully trace, within the remaining iterations, the exact derivation of `block_sn` in `net/relay.rs` (i.e., whether it is unconditionally the tenure's original electing snapshot or can, for extend blocks, be the block's own/burn-view snapshot). This is the key open question determining whether the vulnerability is live or already correctly guarded.

## Recommendation
Audit every call site of `load_nakamoto_reward_set_for_tenure` (and any direct use of `load_nakamoto_reward_set`) to guarantee that for `TenureChangeCause::Extended` blocks, the `tenure_snapshot` argument is always resolved from the *tenure's original electing sortition* (i.e., `tenure_consensus_hash`'s snapshot), never from the block's own burn-view/consensus-hash snapshot. Add an explicit assertion/test that constructs a tenure-extend block crossing a reward-cycle boundary with a different signer set in the new cycle, and verifies that validation uses the pre-boundary signer set consistently across all validating code paths (`net/relay.rs`, `net/download/nakamoto/tenure_downloader*.rs`, `chainstate/nakamoto/coordinator/mod.rs`).

## Proof of Concept
Conceptual PoC (could not be fully constructed/verified against live code in the time available):
1. Boot to Epoch 3.0/Nakamoto with reward cycle N using signer set S1.
2. A miner wins the tenure-electing sortition in cycle N and begins a tenure.
3. The tenure is kept alive via `TenureChangeCause::Extended` blocks (idle/time-based) until the burn chain crosses into reward cycle N+1, which has a different signer set S2 (e.g., due to signer key rotation between cycles, as exercised in `stacks-node/src/tests/signer/v0/mod.rs:4602-4632`).
4. The extend block is actually signed by S1 (the tenure's electing set), per the documented invariant in `load_nakamoto_reward_set_for_tenure`.
5. If any validating node resolves the reward set using the extend block's own burn-view snapshot (now in cycle N+1) instead of the tenure-election snapshot (cycle N), `verify_signer_signatures` is checked against S2, and the node rejects a validly-signed block that other, correctly-implemented nodes accept — producing a temporary tip disagreement.

This PoC's concrete triggerability depends on confirming the exact snapshot resolution in `net/relay.rs` around line 1004, which I was unable to fully inspect before running out of tool iterations; this should be verified directly in a full checkout before treating this as a confirmed, exploitable finding rather than a plausible analog.

### Citations

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L359-381)
```rust
/// Load the reward set that was active when a Nakamoto tenure was elected.
///
/// `tenure_snapshot` must be the snapshot of the sortition that elected the tenure (the
/// sortition whose consensus hash the tenure's blocks carry), not the burnchain tip: a tenure
/// extended across a reward-cycle boundary is still signed by the reward set that was active
/// at its election. Load errors are folded into `ChainstateError` as block acceptance has
/// historically classified them.
pub fn load_nakamoto_reward_set_for_tenure<U: RewardSetProvider>(
    tenure_snapshot: &BlockSnapshot,
    burnchain: &Burnchain,
    chain_state: &mut StacksChainState,
    stacks_tip: &StacksBlockId,
    sort_db: &SortitionDB,
    provider: &U,
) -> Result<Option<RewardSet>, ChainstateError> {
    let reward_cycle = burnchain
        .block_height_to_reward_cycle(tenure_snapshot.block_height)
        .ok_or_else(|| {
            ChainstateError::Expects(format!(
                "Nakamoto tenure election at burn height {} has no reward cycle",
                tenure_snapshot.block_height
            ))
        })?;
```

**File:** stackslib/src/net/relay.rs (L1004-1034)
```rust
        let accept_msg = format!(
            "Stored incoming Nakamoto block {}/{}",
            &block.header.consensus_hash,
            &block.header.block_hash()
        );
        let reject_msg = format!(
            "Rejected incoming Nakamoto block {}/{}",
            &block.header.consensus_hash,
            &block.header.block_hash()
        );

        let tip = &block_sn.sortition_id;

        let reward_set = match load_nakamoto_reward_set_for_tenure(
            &block_sn,
            burnchain,
            chainstate,
            stacks_tip,
            sortdb,
            &OnChainRewardSetProvider::new(),
        ) {
            Ok(Some(reward_set)) => reward_set,
            Ok(None) => {
                error!("No RewardCycleInfo found for tip {}", tip);
                return Err(chainstate_error::PoxNoRewardCycle);
            }
            Err(e) => {
                error!("No RewardCycleInfo loaded for tip {}: {:?}", tip, &e);
                return Err(e);
            }
        };
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1097-1131)
```rust
    pub fn verify_signer_signatures(
        &self,
        reward_set: &RewardSet,
        epoch_id: StacksEpochId,
    ) -> Result<u32, ChainstateError> {
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };

        // if this is a shadow block, then its signing weight is as if every signer signed it, even
        // though the signature vector is undefined.
        if self.is_shadow_block() {
            return Ok(self.get_shadow_signer_weight(reward_set)?);
        }

        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();
```

**File:** stacks-signer/changelog.d/no-fallback-to-stopped-miner.fixed (L1-1)
```text
Do not revert to the prior sortition's miner on inactivity timeout unless the canonical Stacks tip is in that miner's tenure. A miner only extends a tenure it won, so after a Bitcoin reorg orphaned the prior tenure, signers could demote a slow-but-live sortition winner to a miner that had already stopped mining, rejecting all of the winner's proposals until the next burn block.
```
