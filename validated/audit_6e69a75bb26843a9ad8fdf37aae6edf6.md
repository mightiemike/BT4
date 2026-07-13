The code confirms the vulnerability. `SetVestingAccounts` is called **only** in `InitGenesis` and nowhere else in the entire codebase. There is no hook, no `MsgServer`, and no account-creation callback in the supply module that updates the stored list when new vesting accounts are created post-genesis.

---

### Title
`GetUnvestedSupply` Uses a Genesis-Frozen Vesting Account List, Causing Permanent `LiquidSupply` Inflation for Post-Genesis Vesting Accounts — (`x/supply/keeper/keeper.go`, `x/supply/keeper/genesis.go`)

### Summary
The supply module snapshots all vesting accounts exactly once at chain start in `InitGenesis`. Any vesting account created after genesis via the standard Cosmos SDK `MsgCreateVestingAccount` or `MsgCreatePeriodicVestingAccount` is never added to the stored list. As a result, `GetUnvestedSupply` permanently ignores those accounts' locked coins, and `LiquidSupply` permanently overestimates circulating supply by the full locked amount for the lifetime of the chain.

### Finding Description

`InitGenesis` calls `FetchVestingAccounts` (which iterates all auth accounts) and persists the result once: [1](#0-0) 

`SetVestingAccounts` is only ever called from that single site: [2](#0-1) 

`GetUnvestedSupply` reads exclusively from this frozen KV-store list: [3](#0-2) 

`GetLiquidSupply` subtracts only what `GetUnvestedSupply` returns: [4](#0-3) 

There is no account-creation hook, no `MsgServer`, and no other call site for `SetVestingAccounts` anywhere in the supply module:



When a user submits `MsgCreateVestingAccount` (handled entirely by the Cosmos SDK auth/vesting module), the supply module is never notified. The new account's address is never appended to `VestingAccountsKey`. Every subsequent call to `LiquidSupply` omits that account's `LockedCoins` from the subtraction, inflating the reported liquid supply by exactly that amount for the rest of the chain's life.

### Impact Explanation
The invariant `liquid_supply = total_supply − unvested_supply − module_account_balance` is permanently broken for every post-genesis vesting account. Any consumer of the `LiquidSupply` gRPC endpoint — exchanges, analytics dashboards, governance quorum logic, or on-chain modules — receives a permanently inflated circulating supply figure. The error grows with each new vesting account created and with the size of its locked allocation.

### Likelihood Explanation
`MsgCreateVestingAccount` is a standard, permissionless Cosmos SDK transaction available to any account holder. No governance vote, no privileged key, and no special configuration is required. The condition is trivially reachable on any live chain that has not disabled vesting account creation.

### Recommendation
Replace the one-time genesis snapshot with a live, on-demand approach. `GetUnvestedSupply` should call `FetchVestingAccounts` directly (iterating auth accounts at query time) instead of reading from the frozen KV store, or the supply module must register an account-creation hook that appends new vesting accounts to the stored list whenever one is created post-genesis.

### Proof of Concept
1. Start chain; `InitGenesis` stores zero vesting accounts (empty genesis).
2. Submit `MsgCreateVestingAccount` from any user, locking e.g. 1 000 000 uatom until a future time.
3. Query `LiquidSupply`. The returned value equals `total_supply − module_balances` — the 1 000 000 locked uatom is **not** subtracted.
4. Assert: `liquid_supply + locked_coins_of_new_account ≠ total_supply − module_balances` — the invariant fails by exactly the locked amount, permanently.

### Citations

**File:** x/supply/keeper/genesis.go (L10-12)
```go
func (k Keeper) InitGenesis(ctx sdk.Context, genState types.GenesisState) {
	k.SetVestingAccounts(ctx, k.FetchVestingAccounts(ctx))
}
```

**File:** x/supply/keeper/keeper.go (L71-75)
```go
func (k Keeper) SetVestingAccounts(ctx sdk.Context, vestingAccounts types.VestingAccounts) {
	store := ctx.KVStore(k.storeKey)
	b := k.cdc.MustMarshal(&vestingAccounts)
	store.Set(types.VestingAccountsKey, b)
}
```

**File:** x/supply/keeper/keeper.go (L104-119)
```go
func (k Keeper) GetUnvestedSupply(ctx sdk.Context) sdk.Coins {
	vestingAccounts := k.GetVestingAccounts(ctx)

	var lockedCoins sdk.Coins

	for _, vestingAccountAddress := range vestingAccounts.GetAddresses() {
		addr, err := sdk.AccAddressFromBech32(vestingAccountAddress)
		if err != nil {
			panic(err)
		}

		lockedCoins = lockedCoins.Add(k.bankKeeper.LockedCoins(ctx, addr)...)
	}

	return lockedCoins
}
```

**File:** x/supply/keeper/keeper.go (L144-149)
```go
func (k Keeper) GetLiquidSupply(ctx sdk.Context) sdk.Coins {
	totalSupply := k.GetTotalSupply(ctx)
	unvestedSupply := k.GetUnvestedSupply(ctx)
	moduleAccountBalance := k.GetTotalModuleAccountBalance(ctx, ModuleAccounts...)

	return totalSupply.Sub(unvestedSupply...).Sub(moduleAccountBalance...)
```
