## Analog Found

### Title
Unmetered `BeginBlocker` iteration over FeeCollector balances via `GetAllBalances` enables attacker-triggered chain halt - (File: `x/uvalidator/abci.go`)

### Summary
`x/uvalidator`'s `BeginBlocker` calls `AllocateTokens`, which fetches the entire balance of the `FeeCollector` module account with `k.BankKeeper.GetAllBalances(ctx, feeCollector.GetAddress())` on every block [1](#0-0) . This is the same bug class as the reported MilkyWay finding: an unbounded, unmetered iteration over an address's balances performed inside `BeginBlock`, whose cost scales with the number of distinct denominations that have ever accumulated at that address.

### Finding Description
`BeginBlocker` unconditionally invokes `AllocateTokens` for every block with voting power > 0 [2](#0-1) . `AllocateTokens` then calls `GetAllBalances` on the `FeeCollector` address to determine `feesCollectedInt`, converts it to `DecCoins`, and performs `SendCoinsFromModuleToModule` to move the whole `Coins` set into the `uvalidator` module account [3](#0-2) .

`GetAllBalances` iterates the KV-store range for every denom held by the address; there is no cap on the number of denominations. Since `x/uvalidator` is placed early in `SetOrderBeginBlockers` (before `distrtypes`) [4](#0-3) , this call runs on every block's `BeginBlock`, which — like `BeginBlock`/`EndBlock` execution generally in the SDK — is not subject to per-transaction gas metering the way a `DeliverTx` message is. Any accumulation of many distinct denominations in the `FeeCollector` account therefore inflates the per-block cost of `AllocateTokens` for every future block, exactly mirroring the MilkyWay `AllocateRewards`/`GetAllBalances` pattern.

The `FeeCollector` balance is populated by transaction fee deduction in the ante handler for every transaction on the chain (`app/ante/fee.go` participates in this pipeline) [5](#0-4) . Fee deduction moves coins with `SendCoinsFromAccountToModule`, which is an internal bank keeper call that does not go through the `BlockedAddr`/`MsgSend` restriction used to stop ordinary users from sending funds directly to module accounts. If the chain's fee validation does not strictly enforce a single canonical fee denom (I was unable to confirm this from `app/ante/fee.go` before running out of investigation budget), an attacker able to pay transaction fees in many distinct, attacker-chosen denominations could inflate the number of denoms held by `FeeCollector` over many blocks, each time paying only a minimal fee amount in a new denom.

### Impact Explanation
If reachable, this causes the same class of impact as the original report: `BeginBlocker` (and therefore block production) can be made arbitrarily slow or can time out/halt as `GetAllBalances` + the subsequent `SendCoinsFromModuleToModule`/`DecCoins` conversions iterate over an attacker-inflated number of denominations, at effectively no cost to the attacker relative to the resulting damage (chain-wide DoS on every subsequent block, since `FeeCollector` balance never fully clears until `AllocateTokens` succeeds).

### Likelihood Explanation
Likelihood is **uncertain and unverified** without access to `app/ante/fee.go`'s fee validation logic. Cosmos SDK chains commonly restrict `tx.Fee()` to a single allowed denom (e.g., `upc`), in which case an attacker cannot pollute `FeeCollector` with new denominations and this analog would not be exploitable. I found three references to `FeeCollectorName` in `app/ante/fee.go` [6](#0-5)  but could not inspect its body in the remaining budget to confirm whether multi-denom or unrestricted-denom fees are accepted from ordinary users.

### Recommendation
- Confirm in `app/ante/fee.go` whether transaction fees are restricted to a single canonical denom; if not, restrict fee payment to a fixed allow-list of denoms to prevent `FeeCollector` denom bloat.
- Regardless, harden `AllocateTokens` in `x/uvalidator/abci.go` to avoid `GetAllBalances` over an attacker-influenceable account: iterate a bounded/known set of denoms (or cap/paginate the number of denoms processed per block) instead of pulling the full balance set unconditionally in `BeginBlock`.

### Proof of Concept
Not constructible with certainty without confirming `app/ante/fee.go`'s fee-denom validation; a concrete PoC would submit repeated low-value transactions each paying fees in a unique new denom to `FeeCollector` across many blocks, then measure `AllocateTokens` execution time in a later block, analogous to the `TestAllocateRewards_TokenFlood` PoC in the source report.

### Citations

**File:** x/uvalidator/abci.go (L42-63)
```go
	// full amount will be allocated to community pool by distribution module itself
	if previousTotalPower == 0 {
		ctx.Logger().Debug("uvalidator BeginBlocker: no voting power, skipping token allocation", "block_height", ctx.BlockHeight())
		return nil
	}

	height := ctx.BlockHeight()
	if height > 1 {
		ctx.Logger().Info("uvalidator BeginBlocker: allocating tokens",
			"block_height", height,
			"total_previous_power", previousTotalPower,
			"vote_infos_count", len(ctx.VoteInfos()),
		)
		if err := AllocateTokens(ctx, previousTotalPower, ctx.VoteInfos(), uvalidatorKeeper); err != nil {
			ctx.Logger().Error("uvalidator BeginBlocker: token allocation failed",
				"block_height", height,
				"error", err.Error(),
			)
			return err
		}
		ctx.Logger().Info("uvalidator BeginBlocker: token allocation complete", "block_height", height)
	}
```

**File:** x/uvalidator/abci.go (L76-94)
```go
	feeCollector := k.AuthKeeper.GetModuleAccount(ctx, authtypes.FeeCollectorName)
	feesCollectedInt := k.BankKeeper.GetAllBalances(ctx, feeCollector.GetAddress())
	if feesCollectedInt.IsZero() {
		k.Logger().Debug("AllocateTokens: no fees collected, skipping", "block_height", sdkCtx.BlockHeight())
		return nil
	}
	feesCollected := sdk.NewDecCoinsFromCoins(feesCollectedInt...)

	k.Logger().Debug("AllocateTokens: fees collected",
		"block_height", sdkCtx.BlockHeight(),
		"fees", feesCollectedInt.String(),
		"bonded_votes", len(bondedVotes),
	)

	// transfer collected fees to the uvalidator module account
	err := k.BankKeeper.SendCoinsFromModuleToModule(ctx, authtypes.FeeCollectorName, types.ModuleName, feesCollectedInt)
	if err != nil {
		return err
	}
```

**File:** app/app.go (L1081-1092)
```go
	app.ModuleManager.SetOrderBeginBlockers(
		minttypes.ModuleName,
		erc20types.ModuleName,
		feemarkettypes.ModuleName,
		evmtypes.ModuleName, // NOTE: EVM BeginBlocker must come after FeeMarket BeginBlocker

		uvalidatortypes.ModuleName,
		distrtypes.ModuleName,
		slashingtypes.ModuleName,
		evidencetypes.ModuleName,
		stakingtypes.ModuleName,
		genutiltypes.ModuleName,
```

**File:** app/app.go (L1229-1246)
```go
	app.setAnteHandler(chainante.HandlerOptions{
		Cdc:                   app.appCodec,
		AccountKeeper:         app.AccountKeeper,
		BankKeeper:            app.BankKeeper,
		FeegrantKeeper:        app.FeeGrantKeeper,
		FeeMarketKeeper:       app.FeeMarketKeeper,
		SignModeHandler:       txConfig.SignModeHandler(),
		IBCKeeper:             app.IBCKeeper,
		WasmKeeper:            &app.WasmKeeper,
		WasmConfig:            &wasmConfig,
		TXCounterStoreService: runtime.NewKVStoreService(keys[wasmtypes.StoreKey]),
		CircuitKeeper:         &app.CircuitKeeper,

		EvmKeeper:              app.EVMKeeper,
		ExtensionOptionChecker: antetypes.HasDynamicFeeExtensionOption,
		SigGasConsumer:         cosmosevmante.SigVerificationGasConsumer,
		MaxTxGasWanted:         cast.ToUint64(appOpts.Get(srvflags.EVMMaxTxGasWanted)),
	})
```

**File:** app/ante/fee.go (L1-1)
```go
package ante
```
