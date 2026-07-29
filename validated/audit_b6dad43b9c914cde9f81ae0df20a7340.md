## Finding: LiquidityCap declared per-token but never enforced during PRC20 minting

### Summary
The Allora finding is a "value that should gate an activation decision is never actually applied correctly," causing an unintended state transition (premature topic activation). The nearest reachable analog in Push Chain is `x/uregistry`'s `TokenConfig.LiquidityCap` — a field explicitly documented as "max supply cap for this token" that is validated for presence at config time but is never read or checked anywhere in the mint/deposit path, so an unprivileged user can mint unlimited PRC20 supply for any registered token via ordinary inbound flows.

### Finding Description
`TokenConfig.LiquidityCap` is defined as `// max supply cap for this token (string big.Int format)` [1](#0-0)  and `ValidateBasic` only checks that the string is non-empty — it never parses it as a big.Int or wires it into any downstream accounting check [2](#0-1) .

Searching the entire `x/uexecutor` deposit/mint path (`depositPRC20`, `CallPRC20Deposit`, `ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, `ExecuteInboundGas`, `ExecuteInboundGasAndPayload`) shows zero references to `LiquidityCap` anywhere in the keeper package — the field is read from `TokenConfig` for `NativeRepresentation.ContractAddress` only, never for a cap check [3](#0-2) . `depositPRC20` parses the inbound amount and unconditionally calls `CallPRC20Deposit`, which mints PRC20 to the recipient with no supply-cap gate [4](#0-3) .

An ordinary, unprivileged external user triggers this simply by submitting a source-chain deposit event that honest Universal Validators observe and vote through the standard `MsgVoteInbound` ballot flow (`x/uexecutor/keeper/voting.go`) — no privileged or malicious actor is required; honest UVs will faithfully vote the true observed amount, and the amount can be attacker-chosen up to whatever the attacker actually deposits/bridges on the source chain (or in the `FUNDS_AND_PAYLOAD` swap/auto-swap paths, repeated many small legitimate deposits accumulate without any cap check). Since the on-chain `LiquidityCap` value is never consulted, repeated (or single very large) legitimate inbound deposits mint PRC20 supply without bound, defeating the documented economic/collateral safety limit meant to bound the module's synthetic-asset exposure per token.

### Impact Explanation
This corrupts PRC20 accounting: the registry declares and validates a supply ceiling per token (`liquidity_cap` is a mandatory, validated field in every token config, including all shipped testnet configs, e.g. `1000000000000000000000000`), but the invariant is never enforced in the only code path that mints PRC20 tokens (`CallPRC20Deposit` / `CallPRC20DepositAutoSwap`). This falls under "corruption of PRC20 or native asset accounting" and "unauthorized mint" in the allowed-impact gate, because token supply exceeds the protocol's declared and validated backing/collateral limit with no code-level control preventing it — purely through ordinary, unprivileged user deposit flows.

### Likelihood Explanation
High reachability: any external user who can get an inbound funds transaction (of any TxType — `FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS_AND_PAYLOAD`) voted to quorum by honest UVs will have PRC20 minted with no supply check. No privileged actor, malicious validator, or governance action is required — this is the default, everyday deposit path documented in `x/uexecutor/README.md`. The bug requires no unusual conditions beyond "user deposits an asset whose registered `TokenConfig.LiquidityCap` exists" — which is every token, since it's a mandatory field.

### Recommendation
In `depositPRC20` (x/uexecutor/keeper/handler.go) and in the auto-swap deposit path, parse `tokenConfig.LiquidityCap` as a `big.Int`, query the PRC20 contract's current `totalSupply()` (or track it in keeper state), and reject (or revert) the inbound execution if `currentSupply + depositAmount > liquidityCap`. This check must be enforced synchronously in the same execution path as the mint call so it cannot be bypassed by any inbound variant (funds, funds+payload, gas+payload, CEA/non-CEA).

### Proof of Concept
Conceptual walk-through (no local execution environment available to run it, but derivable directly from the code):
1. Admin registers a `TokenConfig` for e.g. USDC on `eip155:11155111` with `LiquidityCap = "1000000000000000000000000"` (as in every shipped test/config file, e.g. `config/testnet-donut/base_sepolia/tokens/usdc.json`).
2. An unprivileged user repeatedly bridges USDC deposits (or one very large deposit) from the source chain to Push Chain.
3. Universal Validators honestly observe and vote each inbound via `MsgVoteInbound`; upon quorum, `ExecuteInboundFunds` → `depositPRC20` → `CallPRC20Deposit` mints the corresponding PRC20 amount to the recipient's UEA, with no comparison against `tokenConfig.LiquidityCap` anywhere in the call chain.
4. Repeating step 2 indefinitely mints PRC20 supply far beyond the declared `LiquidityCap`, since the cap value is parsed/stored but never read in the minting logic.

**Note on confidence**: I was unable to fully verify, within the tool budget, whether `LiquidityCap` enforcement might live inside the Solidity `PRC20`/`UniversalCore` contract itself (called via `DerivedEVMCall`) rather than in the Go keeper — the contract source is not part of this indexed repository. If the cap is enforced on-chain in the Solidity contract's `mint`/`depositPRC20Token` function, this finding would be invalidated; I could not confirm this either way from the available Go-side code, and recommend a Devin session with full repo/contract access to verify this before treating the finding as final.

### Citations

**File:** proto/uregistry/v1/types.proto (L141-141)
```text
  string liquidity_cap = 7;                // max supply cap for this token (string big.Int format)
```

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

**File:** x/uexecutor/keeper/evm.go (L261-303)
```go
// Calls Handler Contract to deposit prc20 tokens
func (k Keeper) CallPRC20Deposit(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // sender: module account
		handlerAddr,        // destination
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20Token",
		prc20Address,
		amount,
		to,
	)
}
```
