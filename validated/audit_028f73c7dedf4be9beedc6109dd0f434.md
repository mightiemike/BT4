### Title
Incomplete Hardcoded `ModuleAccounts` List Causes `GetLiquidSupply` to Return Inflated Supply — (`File: x/supply/keeper/keeper.go`)

---

### Summary

The `x/supply` keeper defines a hardcoded `ModuleAccounts` list used to compute liquid supply. This list was never updated to include module accounts added after the original implementation (`ibctransfertypes.ModuleName`, `tieredrewardstypes.RewardsPoolName`, `tieredrewardstypes.ModuleName`). As a result, `GetLiquidSupply` returns an inflated value — the balances held in those module accounts are not subtracted — and any integrator or on-chain consumer of this query receives incorrect supply data.

---

### Finding Description

`GetLiquidSupply` computes:

```
liquid_supply = total_supply − unvested_supply − Σ(module_account_balances)
```

The set of module accounts to subtract is the hardcoded `ModuleAccounts` slice: [1](#0-0) 

It contains only six entries: `FeeCollectorName`, `distrtypes.ModuleName`, `BondedPoolName`, `NotBondedPoolName`, `minttypes.ModuleName`, `govtypes.ModuleName`.

The application's authoritative module-account registry (`maccPerms` in `app/app.go`) registers **four additional** accounts that hold real tokens: [2](#0-1) 

| Module account | Holds |
|---|---|
| `ibctransfertypes.ModuleName` ("transfer") | IBC-escrowed tokens |
| `icatypes.ModuleName` | ICA-related tokens |
| `tieredrewardstypes.RewardsPoolName` ("rewards_pool") | Tiered-reward pool tokens |
| `tieredrewardstypes.ModuleName` | Legacy staking-on-behalf tokens |

None of these appear in `ModuleAccounts`. The `GetLiquidSupply` call at line 147 therefore never subtracts their balances: [3](#0-2) 

The query server is still live — `RegisterServices` registers it unconditionally despite the deprecation notice: [4](#0-3) 

---

### Impact Explanation

Any caller of the `supply` gRPC/REST query endpoint receives a liquid-supply figure that is overstated by the sum of balances held in the four omitted module accounts. The most material omission is `ibctransfertypes.ModuleName`: every IBC outbound transfer escrows tokens there, so the overstatement grows with IBC activity. Integrators (exchanges, analytics, wallets) relying on this endpoint for circulating-supply data receive a persistently incorrect value — the same class of "unexpected protocol behavior for integrators" identified in the reference report.

---

### Likelihood Explanation

The endpoint is publicly reachable by any unprivileged account via a standard Cosmos SDK gRPC or REST query. No special permissions are required. The `tieredrewards` and IBC modules are both active in production, so the omitted balances are non-zero on mainnet.

---

### Recommendation

Add the missing module accounts to `ModuleAccounts` in `x/supply/keeper/keeper.go`:

```go
var ModuleAccounts = []string{
    authtypes.FeeCollectorName,
    distrtypes.ModuleName,
    stakingtypes.BondedPoolName,
    stakingtypes.NotBondedPoolName,
    minttypes.ModuleName,
    govtypes.ModuleName,
+   ibctransfertypes.ModuleName,
+   icatypes.ModuleName,
+   tieredrewardstypes.RewardsPoolName,
+   tieredrewardstypes.ModuleName,
}
```

Alternatively, derive the list dynamically from `maccPerms` at keeper construction time so it stays in sync automatically.

---

### Proof of Concept

1. Send tokens cross-chain via IBC; they are escrowed in the `transfer` module account.
2. Query `GET /chainmain/supply/v1/liquid_supply` (or equivalent gRPC).
3. Observe that the returned liquid supply includes the escrowed IBC tokens — they are not subtracted — producing a value higher than the true circulating supply.

The root cause is the static, incomplete `ModuleAccounts` slice at `x/supply/keeper/keeper.go:21-28`, which was never updated when `ibctransfertypes`, `icatypes`, and `tieredrewardstypes` module accounts were added to `maccPerms` in `app/app.go:153-165`.

### Citations

**File:** x/supply/keeper/keeper.go (L20-28)
```go
// ModuleAccounts defines the module accounts which will be queried to get liquid supply
var ModuleAccounts = []string{
	authtypes.FeeCollectorName,
	distrtypes.ModuleName,
	stakingtypes.BondedPoolName,
	stakingtypes.NotBondedPoolName,
	minttypes.ModuleName,
	govtypes.ModuleName,
}
```

**File:** x/supply/keeper/keeper.go (L143-149)
```go
// GetLiquidSupply returns the total liquid supply in the system
func (k Keeper) GetLiquidSupply(ctx sdk.Context) sdk.Coins {
	totalSupply := k.GetTotalSupply(ctx)
	unvestedSupply := k.GetUnvestedSupply(ctx)
	moduleAccountBalance := k.GetTotalModuleAccountBalance(ctx, ModuleAccounts...)

	return totalSupply.Sub(unvestedSupply...).Sub(moduleAccountBalance...)
```

**File:** app/app.go (L153-165)
```go
	maccPerms = map[string][]string{
		authtypes.FeeCollectorName:         nil,
		distrtypes.ModuleName:              nil,
		minttypes.ModuleName:               {authtypes.Minter},
		stakingtypes.BondedPoolName:        {authtypes.Burner, authtypes.Staking},
		stakingtypes.NotBondedPoolName:     {authtypes.Burner, authtypes.Staking},
		govtypes.ModuleName:                {authtypes.Burner},
		ibctransfertypes.ModuleName:        {authtypes.Minter, authtypes.Burner},
		icatypes.ModuleName:                nil,
		tieredrewardstypes.RewardsPoolName: nil,
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: {authtypes.Staking},
	}
```

**File:** x/supply/module.go (L106-109)
```go
func (am AppModule) RegisterServices(cfg module.Configurator) {
	//nolint: staticcheck
	types.RegisterQueryServer(cfg.QueryServer(), am.keeper)
}
```
