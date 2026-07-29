This confirms the finding: `MsgMigrateUEA` is a gasless message (no Cosmos tx fee, per `app/txpolicy/gasless.go`), yet unlike `MsgExecutePayload`, its handler never calls `DeductGasFeesFromReceipt` and its underlying `CallUEAMigrateUEA` passes `gasLimit=nil` (uncapped) with `gasless=false`.### Title
Free, uncapped-gas EVM execution via gasless `MsgMigrateUEA` (no fee, no gas accounting) - ([File: x/uexecutor/keeper/msg_migrate_uea.go])

### Summary
`MsgMigrateUEA` is included in the gasless message whitelist (`app/txpolicy/gasless.go`) alongside vote/consensus messages that are supposed to be gasless because they come from bonded Universal Validators. But `MsgMigrateUEA` is a plain user-submittable message with no bonded/allowlisted-sender restriction, and unlike its sibling `MsgExecutePayload`, its handler performs **no gas fee deduction at all**, and it issues the underlying EVM call with an **uncapped gas limit** (`nil`).

### Finding Description
The gasless allowlist in [1](#0-0)  includes `MsgMigrateUEA` together with UV-only vote messages (`MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`). Those vote messages are restricted to bonded Universal Validators by their respective keeper logic, which is the intended justification for waiving Cosmos-level fees. `MsgMigrateUEA`, however, can be submitted by **any account** — its only requirement is that the referenced UEA is already deployed (`x/uexecutor/keeper/msg_migrate_uea.go:59-62`) and that `CallUEAMigrateUEA` succeeds.

Because the tx is gasless:
- `MinGasPriceDecorator` skips the FeeMarket minimum-fee check [2](#0-1) .
- `DeductFeeDecorator` skips fee deduction entirely, requiring no balance [3](#0-2) .
- `AccountInitDecorator` even auto-creates a fresh zero-balance account for a first-time gasless signer [4](#0-3) .

Then, inside `MigrateUEA`, the keeper calls `CallUEAMigrateUEA`, which invokes `DerivedEVMCall` with `gasLimit=nil` (uncapped, unlike `CallUEAExecutePayload` which passes an explicit, payload-supplied `gasLimit`) and `gasless=false`: [5](#0-4) 

Critically, `MigrateUEA` never calls `DeductGasFeesFromReceipt` afterward — contrast with `ExecutePayload`, whose handler explicitly deducts EVM gas cost from the UEA's PC balance in step 4: [6](#0-5) 
`MigrateUEA`'s equivalent step simply logs and returns, with no fee/gas accounting step at all: [7](#0-6) 

The net effect: any unprivileged attacker who has deployed (or can point to) a UEA can submit `MsgMigrateUEA` at zero cost, with an EVM execution leg whose gas is neither capped by an explicit `gasLimit` nor billed to anyone (no Cosmos fee, no PC balance deduction). The `migrateUEA` call target is a real contract entry point (`UEA_EVM.sol`/`UEA_SVM.sol` migrate function), so `receipt.GasUsed` can be nontrivial per call and is fully attacker-controlled up to the value the migration payload/contract allows.

### Impact Explanation
This is a denial-of-service / resource-exhaustion vector reachable by an ordinary, unprivileged external user with no special permissions, matching the "denial of service ... reachable without privileged control" and "gasless admission ... must not turn attacker input into accepted authorization [for free/uncapped compute]" categories in the allowed-impact gate. An attacker can spam `MsgMigrateUEA` transactions (each requiring only a valid signature over a migration payload for a UEA they control) to consume block gas/CPU for free, repeatedly, without paying fees or having any PC balance — degrading node performance, congesting block space that legitimate gasless UV votes rely on, and burning validator resources with no economic cost to the attacker. It also represents a broken accounting invariant relative to the documented design (`x/uexecutor/README.md` and `DERIVED_TRANSACTIONS.md` describe `gasless=false` derived calls as always being billed via `DeductGasFeesFromReceipt`), so gas cost for this path silently evaporates rather than being charged to the UEA as intended for the parallel `ExecutePayload` path.

### Likelihood Explanation
High likelihood: no privileged role, no validator bonding, and no external-chain dependency are required. The attacker only needs a deployed UEA (self-deployable) and a payload/signature accepted by the UEA's `migrateUEA` entry point (self-signed, since the attacker is the UEA owner). The Cosmos ante pipeline unconditionally treats `MsgMigrateUEA` as gasless per the current whitelist, so this path is always reachable in production as coded.

### Recommendation
- Restrict `MsgMigrateUEA` gasless eligibility, or remove it from the gasless whitelist in `app/txpolicy/gasless.go`, so it is treated like a normal fee-paying Cosmos message (consistent with the fact that, unlike UV votes, its sender is not a bonded/allowlisted party).
- If gasless UX for migration is required, mirror `ExecutePayload`'s design: pass an explicit, payload-bounded `gasLimit` into `CallUEAMigrateUEA` (instead of `nil`), and call `DeductGasFeesFromReceipt` (or an equivalent charge against the UEA's PC balance) after `CallUEAMigrateUEA` succeeds, exactly as done in `ExecutePayload`.
- Add integration tests asserting that `MsgMigrateUEA` either charges gas fees from the UEA or is excluded from the gasless allowlist, and that repeated free calls cannot be used to exhaust block gas.

### Proof of Concept
1. Attacker deploys a UEA for themselves (self-controlled origin), so `isDeployed == true`.
2. Attacker crafts a `MigrationPayload` and self-signs it (they own the UEA's private key), producing valid `Signature`.
3. Attacker submits `MsgMigrateUEA{Signer: attacker, UniversalAccountId: attackerUA, MigrationPayload: payload, Signature: sig}` with zero fee and (if first tx) zero account balance.
4. `IsGaslessTx` returns true (`app/txpolicy/gasless.go:18`) → `MinGasPriceDecorator`/`DeductFeeDecorator` skip all fee/balance checks → `AccountInitDecorator` creates the account if needed.
5. `msgServer.MigrateUEA` → `Keeper.MigrateUEA` → `CallUEAMigrateUEA` executes `migrateUEA` on-chain with `gasLimit=nil` (uncapped) and no `DeductGasFeesFromReceipt` call.
6. Repeat steps 3–5 in a loop (new migration nonce each time, or targeting other self-owned UEAs) to consume EVM gas/CPU across many blocks at zero cost to the attacker.

**Caveat:** Full exploitation depends on how the underlying fork's `DerivedEVMCall` resolves a `nil` gasLimit (e.g., whether it falls back to a fixed default gas cap at the EVM-keeper level) — this default is implemented outside this repository, in the `github.com/pushchain/evm` fork referenced via `go.mod` `replace`, whose source is not present in the indexed codebase. If that fork enforces a modest hard-coded default when `gasLimit` is `nil`, the severity is reduced to "each migrate call is free but capped at the fork's default gas," which still breaks the fee-accounting invariant (no charge at all) but bounds the per-call DoS amplification. I could not verify this fallback value from the available index and recommend confirming it directly in a Devin session with full repository/dependency access.

### Citations

**File:** app/txpolicy/gasless.go (L17-25)
```go
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```

**File:** app/ante/account_init_decorator.go (L52-74)
```go
	newAccAddr := signers[0]
	if !aid.ak.HasAccount(ctx, newAccAddr) {
		ctx.Logger().Debug("account init decorator: new account detected on gasless tx, verifying signature",
			"address", sdk.AccAddress(newAccAddr).String(),
			"simulate", simulate,
		)
		// if account does not exist on chain, bypass rest of ante chain (especially gas and signature verification) here.
		// Perform signature verification on account number e and sequence number e instead.
		if err := aid.verifySignatureForNewAccount(ctx, tx, simulate); err != nil {
			ctx.Logger().Debug("account init decorator: signature verification failed for new account",
				"address", sdk.AccAddress(newAccAddr).String(),
				"error", err,
			)
			return ctx, err
		}

		acc := aid.ak.NewAccountWithAddress(ctx, newAccAddr)
		acc.SetSequence(1)
		aid.ak.SetAccount(ctx, acc)
		ctx.Logger().Info("account init decorator: new account created via gasless tx",
			"address", sdk.AccAddress(newAccAddr).String(),
		)
		return ctx, nil
```

**File:** x/uexecutor/keeper/evm.go (L195-227)
```go
// CallUEAMigrateUEA migrates UEA through existing UEA
func (k Keeper) CallUEAMigrateUEA(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	migration_payload *types.MigrationPayload,
	signature []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiMigrationPayload, err := types.NewAbiMigrationPayload(migration_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		nil,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"migrateUEA",
		abiMigrationPayload,
		signature,
	)
}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-97)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		return execErr
	}
```

**File:** x/uexecutor/keeper/msg_migrate_uea.go (L70-84)
```go
	// Step 3: Migrate UEA through UEA
	receipt, err := k.CallUEAMigrateUEA(sdkCtx, evmFrom, ueaAddr, migrationPayload, signatureVal)
	if err != nil {
		return err
	}

	k.Logger().Info("UEA migrated",
		"chain", caip2Identifier,
		"uea", ueaAddr.Hex(),
		"tx_hash", receipt.Hash,
		"gas_used", receipt.GasUsed,
	)

	return nil
}
```
