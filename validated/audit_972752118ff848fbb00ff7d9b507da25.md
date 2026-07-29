This is a good analog and I have confirmed the root cause.

### Title
UV reward boost uses `IsActiveUniversalValidator` without tombstone/slashing check, allowing a validator slashed for double-signing to keep receiving the boosted reward share for the block it double-signed in - ([File: x/uvalidator/abci.go])

### Summary
The external report describes SKALE's `DelegationController` reducing raw delegated stake on slash but never reducing `_effectiveDelegatedByHolderToValidator`, the value actually used to compute bounty shares — so slashing has no effect on reward distribution. Push Chain's `x/uvalidator` `AllocateTokens` reward-boost logic has the analogous flaw: it determines whether a validator is "active" and boost-eligible purely via `IsActiveUniversalValidator`, which only checks the `UniversalValidatorSet` lifecycle status [1](#0-0) , and never consults `SlashingKeeper.IsTombstoned` the way `GetEligibleVoters` and `IsTombstonedUniversalValidator` do elsewhere in the same module [2](#0-1) [3](#0-2) .

### Finding Description
`AllocateTokens` computes `effectiveTotalPower` and the UV boost reward strictly from the current block's `bondedVotes` (i.e., `ctx.VoteInfos()`, which reflects the previous block's signers) and `IsActiveUniversalValidator(validator.GetOperator())`: [4](#0-3) [5](#0-4) 

`IsActiveUniversalValidator` only reads `UniversalValidatorSet[valAddr].LifecycleInfo.CurrentStatus` and returns `true` if it equals `ACTIVE` — it never checks `SlashingKeeper.IsTombstoned` or the validator's `Jailed`/`Bonded` staking status [1](#0-0) . Everywhere else in the module (`GetEligibleVoters`, `IsTombstonedUniversalValidator`), the code explicitly cross-checks the slashing keeper's tombstone state because "any double-sign by the underlying core validator immediately removes their UV from the eligible voter set" per the module's own README [6](#0-5) . That safeguard was never wired into `AllocateTokens`.

Consider a validator that double-signs at height H (evidence processed at H, causing CometBFT/the slashing module to tombstone and typically zero/slash its bonded power and jail it going forward). Because `ctx.VoteInfos()` in `BeginBlocker` at height H+1 reflects the *previous* block's votes/power (this is documented in-code as "will be from the previous proposer") [7](#0-6) , the tombstoned validator's vote (with its pre-slash power) is still included in that iteration. `IsActiveUniversalValidator` will still return `true` for it (its `UniversalValidatorSet` lifecycle status is unaffected by tombstoning — only a separate admin call to `RemoveUniversalValidator`/`UpdateUniversalValidatorStatus` or the staking-unbond hook (`HandleBaseValidatorUnbonding`) changes lifecycle status, and neither is triggered synchronously by tombstoning). As a result:
1. The tombstoned validator's power is still counted with the `1.148x` `BoostMultiplier` in `effectiveTotalPower` (diluting honest UVs' share).
2. The tombstoned validator itself still receives its proportional `0.148x` extra boost reward via `DistributionKeeper.AllocateTokensToValidator` [8](#0-7) .

This exactly mirrors the SKALE bug pattern: the "effective" value used for reward distribution (`isUV`/boost eligibility) is not reduced or invalidated when the underlying entity is slashed, even though the raw/authoritative state (tombstone flag) has already changed.

### Impact Explanation
This causes misrouted protocol reward flows: a slashed/tombstoned validator's stake continues to earn the 14.8% UV boost reward for at least one block after being tombstoned, and the extra boost dilutes the correct proportional share that honest, non-slashed active UVs should receive (since `effectiveTotalPower` includes the compromised validator's boosted weight in the denominator). This is a corruption of gas/reward accounting reachable without any privileged action by the attacker (the attacker is the double-signing validator itself, an ordinary bonded validator, not a privileged admin) — it falls under "corruption of ... gas fee accounting" / reward distribution in the allowed impact gate.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the attacker to already be a registered, ACTIVE Universal Validator, and the misallocation window is bounded to essentially one block's worth of reward distribution (the `ctx.VoteInfos()` lag) before the vote set naturally excludes the now-unbonded/jailed validator in subsequent blocks. It is a "trigger via honest network mechanics following an attacker action" (double signing), not a persistent drain, so material dollar impact is limited to a single block's boost portion, but it is a genuine unprivileged-attacker-triggerable protocol correctness bug in reward accounting that echoes the reported bug class precisely.

### Recommendation
In `AllocateTokens`, replace or supplement the `IsActiveUniversalValidator` check with the same tombstone-aware eligibility check used in `GetEligibleVoters`/`IsTombstonedUniversalValidator` — i.e., before counting a UV's boosted power or paying it the boost reward, verify `!k.SlashingKeeper.IsTombstoned(ctx, consAddr)` for that validator's consensus address, mirroring the pattern in `x/uvalidator/keeper/validator.go` lines 83-90.

### Proof of Concept
1. Register validator V as an active Universal Validator (`AddUniversalValidator` → admin promotes to `ACTIVE`).
2. V double-signs at block height H; CometBFT submits evidence, the slashing module tombstones/jails V (V's `UniversalValidatorSet` lifecycle entry remains `ACTIVE` because nothing synchronously calls `UpdateUniversalValidatorStatus`/`RemoveUniversalValidator` on tombstone).
3. At `BeginBlocker` for height H+1, `ctx.VoteInfos()` still contains V's vote from block H (with pre-slash power).
4. `AllocateTokens` calls `IsActiveUniversalValidator(V.GetOperator())`, which returns `true` (lifecycle status still `ACTIVE`), so V's power is boosted `1.148x` in `effectiveTotalPower` and V receives its `0.148x` proportional share via `AllocateTokensToValidator`, despite being tombstoned.
5. Compare against `GetEligibleVoters`, which for the same V and same block would correctly exclude V due to `SlashingKeeper.IsTombstoned` — demonstrating the inconsistency between the two eligibility paths (matching the SKALE report's `_delegatedByHolderToValidator` vs `_effectiveDelegatedByHolderToValidator` divergence).

### Citations

**File:** x/uvalidator/keeper/validator.go (L80-90)
```go
		if !sv.IsBonded() {
			return false, nil
		}
		consAddr, caErr := sv.GetConsAddr()
		if caErr != nil {
			k.Logger().Debug("eligible voter filter: GetConsAddr failed", "validator", addr.String(), "err", caErr)
			return false, nil
		}
		if k.SlashingKeeper.IsTombstoned(sdkCtx, consAddr) {
			return false, nil
		}
```

**File:** x/uvalidator/keeper/validator.go (L295-326)
```go
// IsActiveUniversalValidator returns true if the given validator address is
// currently registered as an active universal validator.
func (k Keeper) IsActiveUniversalValidator(
	ctx context.Context,
	validatorOperatorAddr string,
) (bool, error) {
	valAddr, err := sdk.ValAddressFromBech32(validatorOperatorAddr)
	if err != nil {
		return false, err
	}

	exists, err := k.UniversalValidatorSet.Has(ctx, valAddr)
	if err != nil {
		return false, err
	}
	if !exists {
		return false, nil
	}

	uv, err := k.UniversalValidatorSet.Get(ctx, valAddr)
	if err != nil {
		return false, fmt.Errorf("failed to get universal validator: %w", err)
	}

	isActive := uv.LifecycleInfo.CurrentStatus == types.UVStatus_UV_STATUS_ACTIVE
	k.Logger().Debug("checked active universal validator status",
		"validator", validatorOperatorAddr,
		"is_active", isActive,
		"current_status", uv.LifecycleInfo.CurrentStatus.String(),
	)

	return isActive, nil
```

**File:** x/uvalidator/keeper/voting.go (L106-117)
```go
	// Get consensus address and check tombstoned status via slashing keeper
	consAddress, err := validator.GetConsAddr()
	if err != nil {
		return false, fmt.Errorf("failed to get consensus address: %w", err)
	}

	isTombstoned := k.SlashingKeeper.IsTombstoned(sdkCtx, consAddress)
	k.Logger().Debug("universal validator tombstone status",
		"validator", universalValidator,
		"is_tombstoned", isTombstoned,
	)
	return isTombstoned, nil
```

**File:** x/uvalidator/abci.go (L73-75)
```go
	// fetch and clear the collected fees for distribution, since this is
	// called in BeginBlock, collected fees will be from the previous block
	// (and distributed to the previous proposer)
```

**File:** x/uvalidator/abci.go (L96-115)
```go
	// First: calculate effective total power (standard + boost for UVs)
	effectiveTotalPower := math.LegacyZeroDec()
	for _, vote := range bondedVotes {
		validator, err := k.StakingKeeper.ValidatorByConsAddr(ctx, vote.Validator.Address)
		if err != nil {
			return err
		}

		isUV, err := k.IsActiveUniversalValidator(ctx, validator.GetOperator())
		if err != nil {
			return err
		}

		power := math.LegacyNewDec(vote.Validator.Power)
		if isUV {
			power = power.Mul(math.LegacyMustNewDecFromStr(BoostMultiplier))
		}

		effectiveTotalPower = effectiveTotalPower.Add(power)
	}
```

**File:** x/uvalidator/abci.go (L125-158)
```go
	for _, vote := range bondedVotes {
		validator, err := k.StakingKeeper.ValidatorByConsAddr(ctx, vote.Validator.Address)
		if err != nil {
			return err
		}

		isUV, err := k.IsActiveUniversalValidator(ctx, validator.GetOperator())
		if err != nil {
			return err
		}

		if !isUV {
			continue
		}

		uvCount++

		// Use only the extra portion for UV allocation
		power := math.LegacyNewDec(vote.Validator.Power)
		power = power.Mul(math.LegacyMustNewDecFromStr(ExtraBoostPortion))

		powerFraction := power.QuoTruncate(effectiveTotalPower)
		reward := feesCollected.MulDecTruncate(powerFraction)

		k.Logger().Debug("AllocateTokens: allocating UV boost reward",
			"validator", validator.GetOperator(),
			"power", vote.Validator.Power,
			"reward", reward.String(),
		)

		err = k.DistributionKeeper.AllocateTokensToValidator(ctx, validator, reward)
		if err != nil {
			return err
		}
```

**File:** x/uvalidator/README.md (L30-30)
```markdown
Tombstone check (`IsTombstonedUniversalValidator`) consults the slashing keeper directly, so any double-sign by the underlying core validator immediately removes their UV from the eligible voter set.
```
