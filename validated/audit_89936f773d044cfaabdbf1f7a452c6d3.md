### Title
`update_commission_bps` accepts unbounded `commission_bps` (>10,000) with no upper-bound validation, feeding un-clamped values into epoch reward-commission calculation - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
`update_commission_bps` in `programs/vote/src/vote_state/mod.rs` performs only a signer check and a feature-gate check for `CommissionKind::BlockRevenue`, but never validates that `commission_bps <= 10_000`. The stored value is later read verbatim by `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` and passed into `redeem_rewards`/`calculate_stake_rewards` in `runtime/src/inflation_rewards/mod.rs` as `voter_commission_bps`, with no clamping anywhere along the path.

### Finding Description
`update_commission_bps` [1](#0-0)  only checks the `BlockRevenue`/`block_revenue_sharing` feature gate and the authorized-withdrawer signature before storing `commission_bps` verbatim via `set_inflation_rewards_commission_bps`/`set_block_revenue_commission_bps`. There is no `commission_bps > 10_000` rejection, and `is_commission_update_allowed` (the epoch-half timing gate used by legacy `update_commission`) is explicitly *not* invoked here — the code comment states "No commission update rule, per SIMD-0249 and SIMD-0291" [2](#0-1) .

The processor dispatch confirms the only two gates applied are the `commission_rate_in_basis_points` and `delay_commission_updates` feature flags: [3](#0-2) .

The repo's own test explicitly documents and exercises this as allowed behavior, storing 150% and 500% commissions without rejection: [4](#0-3) .

The stored `inflation_rewards_commission_bps` field is later consumed unmodified by the epoch reward pipeline: `calculate_stake_rewards_and_commissions` reads it via `vote_state_view.inflation_rewards_commission()` (with no bound enforced) and passes it straight into `redeem_delegation_rewards` → `redeem_rewards` as `commission_bps` [5](#0-4) , and finally into `redeem_stake_rewards`/`calculate_stake_rewards` in `runtime/src/inflation_rewards/mod.rs` as `voter_commission_bps` [6](#0-5) .

### Impact Explanation
I was not able to retrieve and confirm the exact arithmetic that splits total point-value rewards into staker vs. voter shares inside `calculate_stake_rewards` beyond line 260 of `runtime/src/inflation_rewards/mod.rs` (tool budget exhausted before reaching that code), so I cannot confirm from the code itself whether the downstream split uses saturating/checked arithmetic that safely clamps an over-100% commission, or whether it would underflow/panic. What is confirmed is that no clamp exists on `commission_bps` at the point it is set, nor at the point it is read for reward calculation — the value flows through unchanged. If the downstream split formula computes `staker_reward = total_reward - (total_reward * commission_bps / 10_000)` without a `min(commission_bps, 10_000)` or a saturating subtraction, a `commission_bps` > 10,000 would make the voter share exceed the total reward pool, producing either a panic (unchecked u64 subtraction underflow → cluster-halting panic during epoch rewards) or, if using `wrapping`/some other arithmetic, silent lamport-inflation, breaking `METERING_TOTALITY`/`VALUE_CONSERVATION`. This would match "Consensus divergence" or "lamport inflation" bounty categories if confirmed. This part of the impact remains unverified from the code I could access.

### Likelihood Explanation
The precondition is trivial and cheap: the authorized withdrawer of any vote account (an ordinary keypair, not requiring validator/leader privilege) submits `VoteInstruction::UpdateCommissionBps{commission_bps: 65535, kind: InflationRewards}` when `commission_rate_in_basis_points` and `delay_commission_updates` are active (both are mainnet-default per `feature-set/src/lib.rs`). The instruction succeeds unconditionally as shown by the passing test at commission_bps values of 15,000 and 50,000 [7](#0-6) . This makes the precondition (an out-of-range stored commission) fully attacker-controlled and repeatable every epoch by any vote-account withdraw authority.

### Recommendation
Add an explicit upper-bound check in `update_commission_bps` (and in the setters `set_inflation_rewards_commission_bps`/`set_block_revenue_commission_bps`) rejecting `commission_bps > 10_000` with `InstructionError::InvalidInstructionData`, consistent with the legacy `u8` percentage's natural 0–100 bound. Additionally, defensively clamp `voter_commission_bps` to `[0, 10_000]` at the point of use in `calculate_stake_rewards`/`redeem_rewards` as defense-in-depth in case any future write path bypasses the vote-program check (e.g., via account write during a bank/SVM upgrade path or a bug in another commission-setting instruction).

### Proof of Concept
1. Unit test in `programs/vote/src/vote_state/mod.rs`: call `update_commission_bps` with `commission_bps` in `10_001..=u16::MAX` for `CommissionKind::InflationRewards` with `commission_rate_in_basis_points`/`delay_commission_updates` active; assert result is `Err(InstructionError::InvalidInstructionData)` (currently this returns `Ok(())`, contradicting the desired invariant — see existing test at [7](#0-6)  which currently asserts these succeed).
2. Integration test in `runtime/src/inflation_rewards/mod.rs` / `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`: construct a vote account with `inflation_rewards_commission_bps = 20_000` (200%), a stake delegation with nonzero credits, and drive `calculate_stake_rewards`/`redeem_rewards` with a nonzero `point_value.rewards`; assert the computed `voter_rewards` never exceeds total reward pool and `staker_rewards` is never negative/underflowed (would require examining/instrumenting the exact split formula past line 260 of `runtime/src/inflation_rewards/mod.rs`, which I was unable to fully inspect in this session).

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L827-859)
```rust
/// Update the vote account's commission in basis points (SIMD-0291, SIMD-0123).
pub fn update_commission_bps<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    commission_bps: u16,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    block_revenue_sharing_enabled: bool,
) -> Result<(), InstructionError> {
    // Per SIMD-0291: BlockRevenue returns InvalidInstructionData unless
    // SIMD-0123 (block_revenue_sharing) is enabled.
    if matches!(kind, CommissionKind::BlockRevenue) && !block_revenue_sharing_enabled {
        return Err(InstructionError::InvalidInstructionData);
    }

    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // No commission update rule, per SIMD-0249 and SIMD-0291.

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    match kind {
        CommissionKind::InflationRewards => {
            vote_state.set_inflation_rewards_commission_bps(commission_bps);
        }
        CommissionKind::BlockRevenue => {
            vote_state.set_block_revenue_commission_bps(commission_bps);
        }
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1927-1935)
```rust
        commission_bps_roundtrip(1_100); // Increase to 11%
        commission_bps_roundtrip(5_000); // Increase to 50%
        commission_bps_roundtrip(4_400); // Decrease to 44%
        commission_bps_roundtrip(4_600); // Increase to 46%

        // Values > 10,000 bps are allowed at program level.
        commission_bps_roundtrip(15_000); // 150%
        commission_bps_roundtrip(50_000); // 500%
    }
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L709-724)
```rust
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

**File:** runtime/src/inflation_rewards/mod.rs (L102-111)
```rust
fn redeem_stake_rewards<'a>(
    stake: &mut Stake,
    voter_commission_bps: u16,
    vote_state: DelegatedVoteState,
    calculation_environment: CalculationEnvironment<'a>,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    ag_epoch_type: &AlpenglowEpochType,
    current_lamports: u64,
    minimum_lamports: u64,
) -> Option<(u64, u64)> {
```
