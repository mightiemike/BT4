### Title
`TokenConfig.LiquidityCap` is stored and validated as required but never enforced when minting PRC20 during inbound deposit execution - (File: x/uexecutor/keeper/handler.go)

### Summary
The external report flags an `OmoVault` pattern where a `supplyCap` state variable exists (with a setter) but is never checked in `deposit()`/`mint()`, allowing unbounded minting past the intended cap. Push Chain's `uregistry.TokenConfig` has an analogous `LiquidityCap` field that is required to be non-empty at registration time [1](#0-0) , but no code path in the inbound deposit/minting flow (`depositPRC20` → `CallPRC20Deposit`) reads or compares against it before minting PRC20 to a recipient.

### Finding Description
`TokenConfig` defines a `LiquidityCap` field that `ValidateBasic` requires to be a non-empty string [1](#0-0) , implying it is meant to bound how much of a given external asset's PRC20 representation can be minted on Push Chain. However, the inbound execution path that actually mints PRC20 tokens — `Keeper.depositPRC20` in `x/uexecutor/keeper/handler.go` — fetches the `TokenConfig` only to read `NativeRepresentation.ContractAddress`, and never reads or checks `LiquidityCap` before calling `CallPRC20Deposit` with the attacker/validator-observed `amount` [2](#0-1) . A repo-wide search confirms `LiquidityCap`/`liquidityCap` is never referenced anywhere under `x/uexecutor/` (the module that performs minting), only in `uregistry` validation and tests. This mirrors the `OmoVault` bug class exactly: a cap value is defined and validated as present, but structurally disconnected from the mint path that it is supposed to bound.

### Impact Explanation
Since `LiquidityCap` is unenforced, an inbound whose amount is honestly observed and voted by validators (i.e., a real large deposit/lock event on the source chain, or a series of them) can mint PRC20 supply on Push Chain far beyond whatever cap operators intended to configure per token/chain. This directly affects PRC20/native asset accounting invariants (in-scope: "corruption of PRC20 or native asset accounting ... token mapping ... canonical UniversalTx state") because the chain has no independent circuit breaker on minted supply per token, relying entirely on the correctness/liveness of the source-chain lock contract's own cap (an external, unauthenticated invariant from Push Chain's perspective). If the source-chain lock/cap or price data is ever inconsistent with the configured `LiquidityCap`, or if a token's real bridged supply should be capped for risk-management reasons, Push Chain's own ledger provides no backstop — impact is bounded by legitimate observed inbound volume, hence rated Low/Medium rather than critical.

### Likelihood Explanation
High likelihood of the invariant gap being reachable under the stated scope: any ordinary user depositing funds on a supported source chain in amounts that, in aggregate or individually, exceed the registered `LiquidityCap` will have those funds honestly observed and voted by Universal Validators and then minted with no cap check. No privileged action, malicious validator, or malformed vote is required — only ordinary user deposit volume. This satisfies "reachable without privileged control" and "ordinary user deposits ... default transaction submission paths alone."

### Recommendation
Track cumulative minted/bridged PRC20 supply per `(sourceChain, assetAddr)` (or per PRC20 contract) in `x/uexecutor` or `x/uregistry` state, and enforce `LiquidityCap` in `depositPRC20` (and any other mint-issuing path such as `CallPRC20DepositAutoSwap`) before calling `CallPRC20Deposit`, rejecting or reverting inbound execution (with an appropriate `FAILED` `PCTx` / revert-outbound path) when the configured cap would be exceeded.

### Proof of Concept
Conceptual trace (not independently executed, based on static code reading):
1. Register a `TokenConfig` for `eip155:X` / `assetAddr` with `LiquidityCap = "1000000"` (1 unit at 6 decimals).
2. Submit (or have validators observe/vote) an `Inbound` of `TxType_FUNDS` with `Amount = "1000000000"` (1000x the cap) for that same `(sourceChain, assetAddr)`.
3. Validators vote to quorum via `MsgVoteInbound`; `ExecuteInboundFunds`/`ExecuteInboundFundsAndPayload` calls `depositPRC20` → `CallPRC20Deposit`, which mints the full `1000000000` PRC20 amount to the recipient's UEA with no comparison against `LiquidityCap` anywhere in the call chain.
4. Resulting minted PRC20 balance exceeds the configured cap with no on-chain rejection — confirmed by the absence of any `LiquidityCap` reference in `x/uexecutor` and the full body of `depositPRC20` shown above, which contains no cap-related branch.

Note: I could not find any keeper-level or precompile-level cap enforcement elsewhere in the indexed code (e.g., inside the PRC20 contract's `deposit`/mint EVM logic) — that Solidity-side logic lives in `push-chain-core-contracts` (a separate repo) and was not available in this index. If the PRC20 contract itself enforces a cap via its own `totalSupply`/cap logic invoked through `depositPRC20Token`, this finding would be mitigated at the contract layer; that could not be verified from this repository alone.

### Citations

**File:** x/uregistry/types/token_config.go (L56-58)
```go
	if strings.TrimSpace(p.LiquidityCap) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "liquidity_cap cannot be empty")
	}
```

**File:** x/uexecutor/keeper/handler.go (L12-46)
```go
func (k Keeper) depositPRC20(
	ctx sdk.Context,
	sourceChain string,
	assetAddr string,
	recipient common.Address,
	amountStr string,
) (*vmtypes.MsgEthereumTxResponse, error) {
	// get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, sourceChain, assetAddr)
	if err != nil {
		return nil, err
	}

	if tokenConfig.NativeRepresentation == nil {
		return nil, fmt.Errorf("token config for %s:%s has no native representation", sourceChain, assetAddr)
	}
	prc20Address := tokenConfig.NativeRepresentation.ContractAddress
	prc20AddressHex := common.HexToAddress(prc20Address)

	// convert amount
	amount := new(big.Int)
	amount, ok := amount.SetString(amountStr, 10)
	if !ok {
		return nil, fmt.Errorf("invalid amount: %s", amountStr)
	}

	k.Logger().Debug("EVM call: depositPRC20Token",
		"prc20", prc20AddressHex.Hex(),
		"recipient", recipient.Hex(),
		"amount", amountStr,
	)

	// call PRC20 deposit
	return k.CallPRC20Deposit(ctx, prc20AddressHex, recipient, amount)
}
```
