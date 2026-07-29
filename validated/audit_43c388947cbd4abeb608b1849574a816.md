### Title
`LiquidityCap` on `TokenConfig` is stored but never enforced when minting PRC20 on inbound deposits - ([File: x/uexecutor/keeper/handler.go])

### Summary
`x/uregistry` lets an admin register a `TokenConfig` with a `liquidity_cap` field explicitly documented as the "max supply cap for this token." [1](#0-0)  `TokenConfig.ValidateBasic` only checks that the string is non-empty, never that a mint amount is checked against it. [2](#0-1)  The actual PRC20 minting path, `depositPRC20` → `CallPRC20Deposit`, reads the token config, converts the inbound amount, and mints directly with no comparison to `LiquidityCap` anywhere in the call chain. [3](#0-2) [4](#0-3) 

### Finding Description
This is the direct analog of the report's second item: "`createAllowlist` accepts a `units` argument which should be the maximum units mintable through the allowlist — this should be enforced with a check on minting claims from allowlist." On Push Chain, `TokenConfig.LiquidityCap` plays the same declared role for a bridged token's PRC20 representation — a cap meant to bound how much of that asset can ever be minted on Push Chain via inbound deposits.

Tracing an inbound deposit (`ExecuteInboundFunds` → `depositPRC20` → `CallPRC20Deposit` → `DerivedEVMCall("depositPRC20Token", ...)`) shows the amount is taken straight from the attacker/UV-observed `inbound.Amount` (already validated only for being a non-negative uint256 in `ValidateForExecution`, not against any liquidity cap) and minted without ever loading or comparing against `tokenConfig.LiquidityCap`. [5](#0-4) [3](#0-2) 

Since inbound votes are honest-validator-driven observations of real gateway deposit events on the external chain, an attacker cannot spoof an arbitrarily large deposit through this path — the amount is (per the threat model) bound by what actually landed in the gateway contract. However, the field's entire purpose per the README ("liquidity cap: max supply cap for this token") signals an intended protocol-level supply invariant that the enforcement is completely missing for. If any surrounding assumption breaks (e.g., the value later feeds fee/collateral/pricing logic elsewhere, or a token is later used with a wrapped/synthetic mechanism where the external-chain balance and the PRC20 supply can diverge — for instance via `CallPRC20DepositAutoSwap`, `CallUniversalCoreRefundUnusedGas`, or repeated per-inbound mints that individually stay under any external per-tx limit but cumulatively blow past the intended `liquidity_cap`), there is no on-chain backstop. The absence of any code path reading `LiquidityCap` (grep across the whole repo turns up **zero** non-proto, non-test, non-doc references to enforcing it) means this is a genuinely dead invariant, not merely deferred to another layer.

### Impact Explanation
Medium/Low under the current allowed-impact gate: because inbound mint amounts are gated by honest-validator observation of real external-chain events (not spoofable by an unprivileged attacker alone), the missing cap check does not by itself let an attacker mint unbacked PRC20 or steal funds today. The impact is best framed as: unauthorized/unbounded PRC20 supply growth relative to the declared `liquidity_cap` invariant, which is a corruption of PRC20 accounting semantics that the registry explicitly promises but never enforces — a latent risk if any future or existing code path relies on that cap holding (auto-swap pricing, gas refund swap quoting, liquidity-based risk limits) without an unprivileged attacker having to compromise a validator or gateway.

### Likelihood Explanation
Low today, since triggering an actual violation requires either (a) an external-chain gateway contract genuinely receiving deposits that exceed the cap (an event outside attacker control alone) or (b) many honest, quorum-approved inbounds accumulating past the cap over time — not a single unprivileged attacker action. This keeps it below "reachable purely by unprivileged user action" for a direct fund-theft/DoS claim, but it remains a concrete missing invariant enforcement matching the reported bug class.

### Recommendation
Enforce `TokenConfig.LiquidityCap` before minting: in `depositPRC20` (`x/uexecutor/keeper/handler.go`), after fetching `tokenConfig`, query the PRC20's current `totalSupply()` (already exposed in the PRC20 ABI) and reject (or clamp/revert with a failed PCTx + revert outbound, consistent with the existing `ValidateForExecution` failure-handling pattern) any deposit that would push `totalSupply + amount` past `tokenConfig.LiquidityCap`. Apply the same check in `CallPRC20DepositAutoSwap`'s call path since it also mints PRC20. This mirrors the report's recommendation to check `units`/cap "on minting."

### Proof of Concept
Not directly demonstrable as an unprivileged-attacker exploit under the current threat model, since honest UV quorum gates the inbound amount to a real external-chain event; static analysis confirms:
1. `TokenConfig.LiquidityCap` is set at token registration and documented as a supply cap. [6](#0-5) 
2. `TokenConfig.ValidateBasic` never parses/compares this value against anything. [7](#0-6) 
3. `depositPRC20`/`CallPRC20Deposit` mint using only `inbound.Amount`, with no read of `tokenConfig.LiquidityCap` or the PRC20's `totalSupply()` anywhere in the call chain. [3](#0-2) [4](#0-3) 

A repo-wide search for `LiquidityCap`/`liquidityCap` shows matches only in protobuf-generated code, tests, and config JSON — none in mint-path business logic — confirming the check is entirely absent from production code.

### Citations

**File:** api/uregistry/v1/types.pulsar.go (L5721-5721)
```go
	LiquidityCap         string                `protobuf:"bytes,7,opt,name=liquidity_cap,json=liquidityCap,proto3" json:"liquidity_cap,omitempty"`                         // max supply cap for this token (string big.Int format)
```

**File:** x/uregistry/types/token_config.go (L22-68)
```go
// ValidateBasic performs sanity checks on the TokenConfig
func (p TokenConfig) ValidateBasic() error {
	if strings.TrimSpace(p.Chain) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "chain cannot be empty")
	}

	if strings.TrimSpace(p.Address) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token contract address cannot be empty")
	}

	// Enforce a parseable address for the chain's namespace (e.g. 20-byte hex
	// for eip155, base58 for solana) so every registration lands on the
	// canonical storage key.
	if _, err := utils.CanonicalizeAddressByNamespace(p.Chain, p.Address); err != nil {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid token address for chain %s: %s", p.Chain, err)
	}

	if strings.TrimSpace(p.Name) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token name cannot be empty")
	}

	if strings.TrimSpace(p.Symbol) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token symbol cannot be empty")
	}

	if p.Decimals == 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "decimals must be greater than zero")
	}

	// Validate token_type is within known enum range
	if _, ok := TokenType_name[int32(p.TokenType)]; !ok {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid token_type: %v", p.TokenType)
	}

	if strings.TrimSpace(p.LiquidityCap) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "liquidity_cap cannot be empty")
	}

	if p.NativeRepresentation == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "native_representation is required")
	}
	if err := p.NativeRepresentation.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid native representation")
	}

	return nil
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

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L11-30)
```go
func (k Keeper) ExecuteInboundFunds(ctx context.Context, utx types.UniversalTx) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	inbound := utx.InboundTx

	k.Logger().Info("execute inbound funds: depositing PRC20",
		"utx_key", utx.Id,
		"source_chain", inbound.SourceChain,
		"recipient", inbound.Recipient,
		"amount", inbound.Amount,
		"is_cea", inbound.IsCEA,
	)

	receipt, err := k.depositPRC20(
		sdkCtx,
		inbound.SourceChain,
		inbound.AssetAddr,
		common.HexToAddress(inbound.Recipient), // recipient is inbound recipient
		inbound.Amount,
	)
```

**File:** config/testnet-donut/base_sepolia/tokens/usdc.json (L1-14)
```json
{
  "chain": "eip155:84532",
  "address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  "name": "USDC.base",
  "symbol": "USDC.base",
  "decimals": 6,
  "enabled": true,
  "liquidity_cap": "1000000000000000000000000",
  "token_type": 1,
  "native_representation": {
    "denom": "",
    "contract_address": "0x84B62e44F667F692F7739Ca6040cD17DA02068A8"
  }
}
```
