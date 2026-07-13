### Title
Migration Chain Halt: `exitVestedAccountsPositions` Aborts on Unbonding Validator via `transferDelegationFromPosition` — (`x/tieredrewards/keeper/transfer_delegation.go`, `x/tieredrewards/migrations/v2/migrate.go`)

---

### Summary

The v2 migration's `exitVestedAccountsPositions` calls `ForceFullExitWithDelegation` for every vesting-account-owned position. That function unconditionally calls `transferDelegationFromPosition`, which hard-rejects any validator that is not currently bonded (`IsBonded() == false`). The error propagates unhandled through the entire migration call stack and into the upgrade handler, causing `RunMigrations` to return an error and halting the chain at upgrade height.

---

### Finding Description

The full call chain is:

**`app/upgrades.go:58`** → `RunMigrations` → **`keeper/migrations.go:18`** → `v2.Migrate` → **`migrations/v2/migrate.go:37-39`** → `exitVestedAccountsPositions` → **`migrations/v2/migrate.go:99-101`** → `ForceFullExitWithDelegation` → **`keeper/force_exit.go:62-64`** → `transferDelegationFromPosition` → **`keeper/transfer_delegation.go:132-134`** → `return types.ErrValidatorNotBonded`

At each layer the error is wrapped and returned with no special handling:

`exitVestedAccountsPositions` iterates all positions and queues every vesting-owned one for forced exit: [1](#0-0) 

`ForceFullExitWithDelegation` only skips positions that are not delegated at all (nil delegation); it has no guard for an unbonding validator: [2](#0-1) 

It then calls `transferDelegationFromPosition` and propagates any error: [3](#0-2) 

`transferDelegationFromPosition` performs a hard `IsBonded()` check and returns `ErrValidatorNotBonded` for any unbonding or unbonded validator: [4](#0-3) 

The upgrade handler returns the error from `RunMigrations` directly, aborting the upgrade: [5](#0-4) 

The existing unit test `TestTransferDelegationFromPosition_ValidatorNotBonded` explicitly confirms that `transferDelegationFromPosition` returns `ErrValidatorNotBonded` when the validator is jailed/unbonding: [6](#0-5) 

The ADR-006 architecture document acknowledges this behavior for the normal user path but does not address the migration path: [7](#0-6) 

---

### Impact Explanation

If any vesting account holds a tier position whose validator is in unbonding or unbonded status at the upgrade block height, the v2 migration returns an error, `RunMigrations` fails, the upgrade handler aborts, and the chain halts. This is a **chain halt at upgrade height** — a critical production impact. No funds are directly stolen, but the chain becomes non-operational until a patched binary is deployed.

---

### Likelihood Explanation

Validators are jailed and enter unbonding status routinely (double-sign, downtime, governance). The upgrade block is scheduled in advance; any validator that goes unbonding in the window between upgrade proposal passage and upgrade height triggers the halt. The precondition (at least one vesting account with a tier position on a non-bonded validator) is realistic and not attacker-controlled — it can occur naturally. An adversary who knows the upgrade height could also deliberately jail a validator (via a double-sign or downtime attack) to trigger the halt.

---

### Recommendation

In `ForceFullExitWithDelegation` (or in `exitVestedAccountsPositions`), check the validator's bond status before calling `transferDelegationFromPosition`. If the validator is not bonded, fall back to a standard `Undelegate` (which does not require the validator to be bonded and initiates the normal unbonding period), or skip the position and log a warning. The migration must not return an error for this case.

---

### Proof of Concept

1. At any block before upgrade height, a vesting account creates a tier position on validator V.
2. Before upgrade height, validator V is jailed (downtime or double-sign); it transitions to unbonding status.
3. The upgrade height is reached; the upgrade handler runs `RunMigrations`.
4. `exitVestedAccountsPositions` walks all positions, finds the vesting-owned position, and calls `ForceFullExitWithDelegation`.
5. `ForceFullExitWithDelegation` calls `transferDelegationFromPosition`; the `IsBonded()` check at `transfer_delegation.go:132` returns `false`; `ErrValidatorNotBonded` is returned.
6. The error propagates through `force_exit.go:62`, `migrate.go:99`, `migrate.go:37`, `migrations.go:18`, and `upgrades.go:58`.
7. `RunMigrations` returns an error; the upgrade handler returns an error; the chain halts.

### Citations

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
```

**File:** x/tieredrewards/keeper/force_exit.go (L22-28)
```go
	if !posState.IsDelegated() {
		logger.Error("force-exit: position is not delegated; cannot force full exit",
			"position_id", posID,
			"owner", posState.Owner,
		)
		return nil
	}
```

**File:** x/tieredrewards/keeper/force_exit.go (L62-64)
```go
	if _, _, _, err := k.transferDelegationFromPosition(ctx, posState, valAddr, positionAmount); err != nil {
		return fmt.Errorf("transfer delegation back to owner for position %d: %w", posID, err)
	}
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L132-134)
```go
	if !validator.IsBonded() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrValidatorNotBonded
	}
```

**File:** app/upgrades.go (L58-61)
```go
		m, err := app.ModuleManager.RunMigrations(ctx, app.configurator, fromVM)
		if err != nil {
			return map[string]uint64{}, err
		}
```

**File:** x/tieredrewards/keeper/transfer_delegation_test.go (L363-379)
```go
func (s *KeeperSuite) TestTransferDelegationFromPosition_ValidatorNotBonded() {
	lockAmount := sdkmath.NewInt(10000)
	pos := s.setupNewTierPosition(lockAmount, true)

	s.advancePastExitDuration()

	valAddr := sdk.MustValAddressFromBech32(pos.Delegation.ValidatorAddress)
	s.jailAndUnbondValidator(valAddr)

	// Compute token value from shares.
	val, err := s.app.StakingKeeper.GetValidator(s.ctx, valAddr)
	s.Require().NoError(err)
	positionAmount := val.TokensFromShares(pos.Delegation.Shares).TruncateInt()

	_, _, _, err = s.keeper.TransferDelegationFromPosition(s.ctx, pos, valAddr, positionAmount)
	s.Require().ErrorIs(err, types.ErrValidatorNotBonded)
}
```

**File:** doc/architecture/adr-006.md (L333-333)
```markdown
| Validator jailed/unbonding | Position stays valid. No base rewards while inactive. No bonus while not bonded. User can redelegate or undelegate (if exit elapsed). ExitTierWithDelegation fails.|
```
