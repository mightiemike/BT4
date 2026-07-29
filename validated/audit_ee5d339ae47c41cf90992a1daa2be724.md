### Title
Missing PRC20 Liquidity-Cap Enforcement Before Deposit Allows Unbounded Minting Beyond Registered `TokenConfig.LiquidityCap` - (File: x/uexecutor/keeper/handler.go)

### Summary
`TokenConfig.LiquidityCap` is defined as the "max supply cap for this token" [1](#0-0)  and is required to be non-empty at registration time via `TokenConfig.ValidateBasic()` [2](#0-1) . However, nowhere in the inbound-deposit execution path is this cap actually checked against the token's current/prospective PRC20 supply before minting. This mirrors the EtherCollateral `openLoan()` bug class: a cap field exists and is validated for non-emptiness at config time, but the mutating operation that increases supply enforces nothing against it at execution time.

### Finding Description
The deposit path is: `ExecuteInboundFunds` / `ExecuteInboundFundsAndPayload` / `ExecuteInboundGas*` call `depositPRC20()` [3](#0-2) , which is implemented as:

```go
func (k Keeper) depositPRC20(
	ctx sdk.Context,
	sourceChain string,
	assetAddr string,
	recipient common.Address,
	amountStr string,
) (*vmtypes.MsgEthereumTxResponse, error) {
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, sourceChain, assetAddr)
	...
	amount, ok := amount.SetString(amountStr, 10)
	...
	return k.CallPRC20Deposit(ctx, prc20AddressHex, recipient, amount)
}
``` [4](#0-3) 

This function fetches the `TokenConfig` (which carries `LiquidityCap`) but never reads or compares `tokenConfig.LiquidityCap` against the token's current total supply plus the incoming `amount` before calling `CallPRC20Deposit`, which performs the actual mint via the `depositPRC20Token` EVM call to the `UNIVERSAL_CORE` handler contract [5](#0-4) . The inbound `Amount` is attacker-controlled input from an external-chain deposit event that is only validated for numeric parseability, not bounded against the registered cap. Every call site of `depositPRC20` (`ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, gas-and-payload variants) inherits this same gap.

Since honest validators vote on and finalize inbound events purely from source-chain observation, an attacker who deposits a large enough amount on the source chain (or repeatedly deposits) can cause `depositPRC20` to mint PRC20 supply on Push Chain past the `LiquidityCap` recorded in `uregistry`'s `TokenConfig`, with no `require`-style guard analogous to the one recommended and implemented for `openLoan()`.

### Impact Explanation
This corrupts the PRC20 token accounting invariant that supply must never exceed `TokenConfig.LiquidityCap` — the exact same invariant class as the report's supply-cap bypass. Uncapped minting of PRC20 tokens undermines any downstream logic, price/collateral assumptions, or backing guarantees tied to the registered cap, and can be triggered by an ordinary unprivileged user simply sending a large inbound deposit (or many deposits) for a registered asset. This falls under "corruption of PRC20 or native asset accounting" and "unauthorized mint... of user or protocol-controlled funds" in the stated impact scope.

### Likelihood Explanation
High: the trigger requires no privileged access — any external-chain sender can submit a deposit event that honest validators will vote in and finalize normally, since the vote/finalization path itself does not know about or enforce `LiquidityCap` either. There is no code path found (in `x/uexecutor` or `x/uregistry`) that reads `LiquidityCap` outside of `ValidateBasic` at config-registration time.

### Recommendation
In `depositPRC20()` (x/uexecutor/keeper/handler.go), before calling `k.CallPRC20Deposit`, fetch the PRC20 contract's current `totalSupply()` (or track supply in module state) and add a check equivalent to:
```go
newSupply := new(big.Int).Add(currentSupply, amount)
if tokenConfig.LiquidityCap != "" {
    cap, _ := new(big.Int).SetString(tokenConfig.LiquidityCap, 10)
    if newSupply.Cmp(cap) > 0 {
        return nil, fmt.Errorf("deposit would exceed liquidity cap for %s:%s", sourceChain, assetAddr)
    }
}
```
On cap-exceeding failure, the existing revert/refund flow (`buildRevertOutbound` / `RevertInstructions`) should trigger, consistent with how other `depositPRC20` failures are already handled in `ExecuteInboundFundsAndPayload`.

### Proof of Concept
Not independently executable without live source-chain relaying/TSS infrastructure; the flaw is confirmed by static code inspection: `depositPRC20` in x/uexecutor/keeper/handler.go loads `tokenConfig.LiquidityCap` transitively via `GetTokenConfig` but never references the field, and a full-repo search confirms `LiquidityCap` is read only in protobuf marshal/unmarshal code and in `ValidateBasic` (registration-time), never in any deposit/mint execution path (`execute_inbound_funds.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`). Conceptually: attacker deposits amount `X` on the source chain for an asset whose `TokenConfig.LiquidityCap = C`, where `X` alone (or cumulative deposits) exceed `C`; honest validators vote the inbound in normally; `ExecuteInboundFundsAndPayload` → `depositPRC20` → `CallPRC20Deposit` mints `X` PRC20 with no cap check, producing PRC20 total supply `> C`.

### Citations

**File:** api/uregistry/v1/types.pulsar.go (L5721-5721)
```go
	LiquidityCap         string                `protobuf:"bytes,7,opt,name=liquidity_cap,json=liquidityCap,proto3" json:"liquidity_cap,omitempty"`                         // max supply cap for this token (string big.Int format)
```

**File:** x/uregistry/types/token_config.go (L56-58)
```go
	if strings.TrimSpace(p.LiquidityCap) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "liquidity_cap cannot be empty")
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
