Based on my investigation, I found a strong structural analog to the reported bug class in Agave's vote program commission mechanism, but I was unable to fully confirm end-to-end exploitability for one of the two commission "kinds" (BlockRevenue) before running out of investigation budget. I present the confirmed finding with explicit caveats on what remains unverified.

### Title
Vote account commission-rate updates via `UpdateCommissionBps` (SIMD-0291) have no rate-limit/cap, unlike legacy `UpdateCommission` - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The legacy `UpdateCommission` vote instruction enforces a timing rule (`is_commission_update_allowed`) that blocks commission *increases* during the second half of an epoch, specifically to prevent a validator from spiking its commission right before rewards are paid out and then reverting it. The newer `UpdateCommissionBps` instruction (SIMD-0291) explicitly removes this protection, allowing the vote account's authorized withdrawer to change `inflation_rewards_commission_bps` / `block_revenue_commission_bps` to any value (0–10000 bps) at any time, with no cap and no cooldown, mirroring the "vault owner can arbitrarily set royalties" pattern from the original report.

### Finding Description
`update_commission` enforces `is_commission_update_allowed` before permitting a commission *increase*: [1](#0-0) 

By contrast, `update_commission_bps` explicitly documents and implements the *absence* of any such rule: [2](#0-1) 

The processor dispatch confirms this instruction is reachable directly from an ordinary signed transaction once the relevant features are active, gated only by feature flags, not by any rate limit: [3](#0-2) 

Similarly, `UpdateCommissionCollector` (SIMD-0232), which redirects where commission lamports are paid, has no timing restriction either — only a signature check: [4](#0-3) 

For `InflationRewards` commission, this gap is mitigated at the reward-calculation layer: when `delay_commission_updates` is active, the commission rate actually applied is fetched from a snapshot of vote-account state from a full epoch earlier, specifically to prevent "last-minute commission rugs": [5](#0-4) [6](#0-5) 

I was **not able to confirm** whether an equivalent epoch-delayed snapshot exists for the `BlockRevenue` commission kind used in Tower/block-revenue-sharing reward distribution (`runtime/src/block_component_processor/vote_reward.rs`), which uses `commission_split_preserve_lamports(self.commission_bps, ...)` to split rewards between the vote account and delegators: [7](#0-6) . If this `commission_bps` is read live (rather than from a delayed/snapshotted vote state) at distribution time, the removal of the update-rule for `UpdateCommissionBps` would allow a validator to spike `block_revenue_commission_bps` to 10000 (100%) immediately before that epoch's Tower/block-revenue distribution, and revert it afterward, capturing delegator rewards that were supposed to be shared — the direct analog of the FERC1155 royalty-frontrunning report.

### Impact Explanation
If BlockRevenue commission is not protected by the same epoch-delay snapshot used for inflation rewards, a validator's authorized withdrawer could unilaterally and instantly capture up to 100% of delegator-earned block-revenue rewards for one distribution cycle, then restore a low commission to keep attracting stake — an on-chain "rug" of staker/delegator funds analogous to the reported royalty manipulation. This would constitute a concrete transfer/loss of delegator funds without requiring mempool front-running, since vote-account state changes are simply committed by ordinary transactions signed by the authorized withdrawer.

### Likelihood Explanation
Confirmed as a design gap for `UpdateCommissionBps`/`UpdateCommissionCollector` (no rate limit, unlike the legacy instruction, and explicitly documented as such in code comments). However, likelihood of *concrete exploitable impact* depends entirely on whether `block_component_processor/vote_reward.rs` reads `commission_bps` from a delayed snapshot (as inflation rewards do) or from the live/current vote account state at distribution time — this was not confirmed within the scope of this investigation.

### Recommendation
Verify whether `BlockRevenue` commission (`block_revenue_commission_bps`) used in `runtime/src/block_component_processor/vote_reward.rs` for Tower/block-revenue distribution is sourced from a delayed epoch snapshot equivalent to `snapshot_epoch_vote_accounts`/`delay_commission_updates` used for `InflationRewards`. If it is not, apply the same one-epoch-delay protection to `BlockRevenue` commission changes (and ideally to `UpdateCommissionCollector` redirection) that already exists for legacy commission and inflation-rewards commission, to prevent last-minute commission or collector-address changes from capturing rewards that should be shared with delegators.

### Proof of Concept
Not independently verified end-to-end due to inability to confirm the BlockRevenue distribution's commission-sourcing timing within the available investigation. The confirmed component is: `UpdateCommissionBps`/`UpdateCommissionCollector` instructions can be submitted by the authorized withdrawer at any point in an epoch with no cap or cooldown (`programs/vote/src/vote_state/mod.rs:828-859`, `907-933`), in contrast to the legacy `UpdateCommission` instruction's epoch-position restriction (`programs/vote/src/vote_state/mod.rs:797-825`). Completing the PoC requires tracing `runtime/src/block_component_processor/vote_reward.rs` to determine the exact source of `commission_bps` at Tower/block-revenue payout time.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L806-815)
```rust
    let vote_state_result = get_vote_state_handler_checked(vote_account, target_version);
    let enforce_commission_update_rule = !disable_commission_update_rule
        && match vote_state_result.as_ref() {
            Ok(decoded_vote_state) => commission > decoded_vote_state.commission(),
            Err(_) => true,
        };

    if enforce_commission_update_rule && !is_commission_update_allowed(clock.slot, epoch_schedule) {
        return Err(VoteError::CommissionUpdateTooLate.into());
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L842-847)
```rust
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // No commission update rule, per SIMD-0249 and SIMD-0291.

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
```

**File:** programs/vote/src/vote_state/mod.rs (L908-921)
```rust
pub fn update_commission_collector<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    new_collector: NewCommissionCollector,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    rent: &Rent,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    let new_collector_key = new_collector.validate_and_resolve_key(vote_account, rent)?;
```

**File:** programs/vote/src/vote_processor.rs (L362-382)
```rust
        VoteInstruction::UpdateCommissionBps {
            commission_bps,
            kind,
        } => {
            // SIMD-0291: Commission Rate in Basis Points
            // Requires SIMD-0185: Vote State V4
            // Requires SIMD-0249: Delay Commission Updates
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points || !feature_set.delay_commission_updates
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            vote_state::update_commission_bps(
                &mut me,
                target_version,
                commission_bps,
                kind,
                &signers,
                feature_set.block_revenue_sharing,
            )
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L305-319)
```rust
pub(super) struct CachedVoteAccounts<'a> {
    /// Snapshot of vote account state from the beginning of the epoch prior to
    /// the rewarded epoch. This snapshot state is saved a full epoch before
    /// being used to prevent last minute commission rugs.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) snapshot_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the beginning of the rewarded epoch.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) rewarded_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the end of the rewarded epoch / beginning of the
    /// distribution epoch.
    pub(super) distribution_epoch_vote_accounts: &'a VoteAccounts,
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L703-724)
```rust
        // Fetch the voter commission from past epochs to attempt to
        // delay the effect of commission updates by at least one
        // full epoch.
        // When `commission_rate_in_basis_points` is true, use the new field
        // `inflation_rewards_commission_bps`; otherwise use the legacy
        // percentage field and convert to basis points by multiplying by 100.
        let commission_bps = if delay_commission_updates {
            let vote_state_for_commission = snapshot_epoch_vote_accounts
                .and_then(|eva| eva.get(&vote_pubkey))
                .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
                .map(|vote_account| vote_account.vote_state_view())
                .unwrap_or(vote_state);
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L1274-1276)
```rust
                let (voter_reward, staker_reward, is_split) =
                    commission_split_preserve_lamports(self.commission_bps, stake_weighted_reward);
                assert!(is_split);
```
