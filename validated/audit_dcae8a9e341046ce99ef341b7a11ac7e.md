Based on my research, I found a direct analog to the M-6 bug class within Push Chain's scoped Go code.

### Title
Unenforced `LiquidityCap` allows unbounded PRC20 minting on inbound deposits - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`TokenConfig.LiquidityCap` is defined as "max supply cap for this token" and is a mandatory, validated field on every token registration [1](#0-0) [2](#0-1) . Just like the Teller Finance report where `liquidityThresholdPercent` exists as a cap but is never included in the utilization math, `LiquidityCap` exists as a cap but is never read or enforced anywhere in the PRC20 minting/accounting path.

### Finding Description
The inbound-funds deposit path reads the `TokenConfig` only to resolve the PRC20 contract address, never to check the requested mint amount against `LiquidityCap`: [3](#0-2) 

`depositPRC20` fetches `tokenConfig` solely for `NativeRepresentation.ContractAddress`, then calls `CallPRC20Deposit` with the full inbound `amount`, with no comparison against `tokenConfig.LiquidityCap` or the token's current outstanding/minted supply. `CallPRC20Deposit` itself just forwards the amount to the EVM `depositPRC20Token` call with no cap check in the Go keeper layer: [4](#0-3) 

This same unchecked path is used by `ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, and `ExecuteInboundGas` (via `CallPRC20DepositAutoSwap`) — i.e., every inbound crosschain deposit flow that mints PRC20 to a recipient [5](#0-4) .

A grep across the whole Go codebase confirms `LiquidityCap`/`liquidity_cap` only appears in proto-generated code, validation tests (`ValidateBasic` just checks the string is non-empty), and integration test fixtures — never in a keeper function that compares it against a running total before minting.

### Impact Explanation
Because `LiquidityCap` is a purely cosmetic/off-chain field with no enforcement in the deposit accounting path, honest validators voting on legitimate, correctly-signed inbound observations from external chains will finalize ballots and mint PRC20 without any bound, regardless of the configured cap. This corrupts PRC20 accounting (unauthorized/uncapped mint of protocol-controlled asset representation), directly matching the "corruption of PRC20 or native asset accounting" and "unauthorized mint" impacts in scope. Any user who can get funds bridged in (a normal, unprivileged action, not requiring any privileged actor) can exceed intended per-token supply limits.

### Likelihood Explanation
High — the trigger requires no privileged access; it only requires performing an ordinary large inbound transfer of a registered token, then having honest validators observe and vote it through the standard `VoteOnInboundBallot`/`ExecuteInboundFunds` flow, which is the default unprivileged deposit path.

### Recommendation
Track cumulative minted/outstanding PRC20 amount per token config and enforce `LiquidityCap` inside `depositPRC20`/`CallPRC20Deposit`/`CallPRC20DepositAutoSwap` before minting, rejecting or capping deposits that would push outstanding supply above `LiquidityCap`, analogous to including the missing cap variable in the utilization/mint math.

### Proof of Concept
1. Admin registers a token config with `LiquidityCap = "1000000"` (1 token at 6 decimals).
2. An external-chain user bridges an inbound transfer of amount `100000000` (100x the cap) for that token.
3. Universal Validators observe and vote the inbound to quorum via `VoteOnInboundBallot`.
4. `ExecuteInboundFunds` → `depositPRC20` → `CallPRC20Deposit` mints the full `100000000` PRC20 to the recipient with no check against `LiquidityCap`, as shown in `x/uexecutor/keeper/handler.go` and `x/uexecutor/keeper/evm.go`.
5. Query the PRC20 `totalSupply()`/`balanceOf()` and observe it exceeds the configured `LiquidityCap`.

### Citations

**File:** x/uregistry/types/types.pb.go (L5721-5721)
```go

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

**File:** x/uexecutor/keeper/evm.go (L262-303)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L24-30)
```go
	receipt, err := k.depositPRC20(
		sdkCtx,
		inbound.SourceChain,
		inbound.AssetAddr,
		common.HexToAddress(inbound.Recipient), // recipient is inbound recipient
		inbound.Amount,
	)
```
