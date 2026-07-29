Confirmed finding: `TokenConfig.Enabled` (analogous to Holdefi's `isActive` flag) is defined in the schema but never read by the deposit/execution path.

### Title
Inbound PRC20 deposits ignore `TokenConfig.Enabled`, allowing minting/crediting through a disabled (de-whitelisted) token config - (File: x/uexecutor/keeper/handler.go)

### Summary
The external report's root cause is that Holdefi's `depositPromotionReserveInternal` never checks the `isActive` whitelist flag before crediting internal accounting for an arbitrary market. Push Chain's `TokenConfig` struct has the equivalent whitelist flag, `Enabled` [1](#0-0) , described explicitly as "Whether this token is enabled for minting/bridging". However, the deposit path that mints/credits PRC20 for inbound funds never reads or checks this field.

### Finding Description
`depositPRC20` in `x/uexecutor/keeper/handler.go` fetches the `TokenConfig` by `(sourceChain, assetAddr)` and only validates that `NativeRepresentation` is non-nil before calling `CallPRC20Deposit`: [2](#0-1) 

It never inspects `tokenConfig.Enabled`. This function is invoked from `ExecuteInboundFunds`, which is reached after honest Universal Validators reach quorum on `MsgVoteInbound` for an inbound event on an external chain: [3](#0-2) 

The same missing check exists in the gas-swap and gas+payload deposit paths (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`), all of which resolve `prc20AddressHex` straight from `tokenConfig.NativeRepresentation.ContractAddress` without ever branching on `Enabled`: [4](#0-3) [5](#0-4) 

Whereas `MsgAddTokenConfig`/`MsgUpdateTokenConfig` are admin-gated [6](#0-5) , the `Enabled` flag on an existing `TokenConfig` entry is meant to let the admin pause a token (e.g. during an incident, exploit, or bridge pause) without deleting the whole entry (deletion via `MsgRemoveTokenConfig` would make `GetTokenConfig` return `ErrNotFound`, correctly blocking deposits). But toggling `Enabled=false` via `MsgUpdateTokenConfig` while keeping the record has **no effect** on the unprivileged inbound flow: an attacker (or any user) who submits/observes an inbound for that disabled asset can still get the deposit executed and PRC20 minted, because `GetTokenConfig` succeeds (the row still exists) and nothing downstream reads `Enabled`.

### Impact Explanation
This breaks the token-mapping/accounting invariant ("token mapping ... must not misroute value or attach the wrong asset semantics"): a token the admin has explicitly disabled for "minting/bridging" can still be minted as PRC20 and credited to a recipient's UEA, and it can still be swapped/refunded through `CallPRC20DepositAutoSwap`/`refundUnusedGas`. If a token is disabled specifically because it is compromised, has a broken vault/gateway, or is being sunset, the disabled flag is expected to be the safety valve; its silent bypass means unprivileged inbound submissions continue to mint/credit value that the protocol operator believed was blocked, which is a direct accounting/authorization defect on user-reachable code (no privileged actor is needed to trigger it — the affected inbound just needs to reach honest-UV quorum as usual).

### Likelihood Explanation
Reachable via completely ordinary inbound flow (no special crafting needed) whenever an admin sets `Enabled=false` on an existing token entry rather than removing it. Given `MsgUpdateTokenConfig` exists precisely to toggle fields like `Enabled` on a live entry (removal/re-add is a more disruptive alternative), disabling-in-place is a plausible operational action, making the gap practically triggerable, not merely theoretical.

### Recommendation
Add an explicit `if !tokenConfig.Enabled { return nil, fmt.Errorf(...) }` check in `depositPRC20` (`x/uexecutor/keeper/handler.go`) and in the equivalent lookup points in `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap`, mirroring the chain-level `IsChainInboundEnabled`/`IsChainOutboundEnabled` checks that are already enforced elsewhere in the codebase (e.g. `MsgExecutePayload` checks `chainConfig.Enabled.IsInboundEnabled` [7](#0-6) ).

### Proof of Concept
1. Admin registers `TokenConfig{Chain: "eip155:X", Address: T, Enabled: true, NativeRepresentation: {ContractAddress: PRC20}}`.
2. Admin later calls `MsgUpdateTokenConfig` to set `Enabled: false` on the same `(Chain, Address)` key, intending to pause deposits for `T` (e.g., because of a known exploit on the source-chain token).
3. Any user still submits (or has an inbound observed for) a deposit event for `(eip155:X, T)`; Universal Validators vote it via `MsgVoteInbound` per normal flow (no privileged bypass required).
4. `ExecuteInboundFunds` → `depositPRC20` → `GetTokenConfig` succeeds (row still exists) → `Enabled` is never checked → `CallPRC20Deposit` mints/credits PRC20 to the recipient's UEA despite the token being marked disabled.

Note: I was unable to locate any additional guard (e.g., in `x/uregistry` keeper writes, or in `CallPRC20Deposit`/`DerivedEVMCall`) that re-validates `Enabled` before the EVM call executes, based on all files retrieved. If such a check exists in a file not surfaced by the index, it would invalidate this finding — a full-repo review (e.g., via a Devin session) would confirm definitively.

### Citations

**File:** x/uregistry/types/types.pb.go (L598-608)
```go
type TokenConfig struct {
	Chain                string                `protobuf:"bytes,1,opt,name=chain,proto3" json:"chain,omitempty"`
	Address              string                `protobuf:"bytes,2,opt,name=address,proto3" json:"address,omitempty"`
	Name                 string                `protobuf:"bytes,3,opt,name=name,proto3" json:"name,omitempty"`
	Symbol               string                `protobuf:"bytes,4,opt,name=symbol,proto3" json:"symbol,omitempty"`
	Decimals             uint32                `protobuf:"varint,5,opt,name=decimals,proto3" json:"decimals,omitempty"`
	Enabled              bool                  `protobuf:"varint,6,opt,name=enabled,proto3" json:"enabled,omitempty"`
	LiquidityCap         string                `protobuf:"bytes,7,opt,name=liquidity_cap,json=liquidityCap,proto3" json:"liquidity_cap,omitempty"`
	TokenType            TokenType             `protobuf:"varint,8,opt,name=token_type,json=tokenType,proto3,enum=uregistry.v1.TokenType" json:"token_type,omitempty"`
	NativeRepresentation *NativeRepresentation `protobuf:"bytes,9,opt,name=native_representation,json=nativeRepresentation,proto3" json:"native_representation,omitempty"`
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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L39-57)
```go
	// --- step 1: get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
		// --- step 2: parse amount
		amount := new(big.Int)
		if amount, ok := amount.SetString(inbound.Amount, 10); !ok {
			execErr = fmt.Errorf("invalid amount: %s", inbound.Amount)
			shouldRevert = true
			revertReason = execErr.Error()
		} else {
			// --- step 3: resolve / deploy UEA
			prc20AddressHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
			chainNamespace, chainId, caipErr := types.ParseCAIP2(inbound.SourceChain)
			if caipErr != nil {
				execErr = fmt.Errorf("invalid SourceChain: %w", caipErr)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-379)
```go
// gasAndPayloadDepositAutoSwap handles the swap quote + deposit autoswap for GAS_AND_PAYLOAD.
func (k Keeper) gasAndPayloadDepositAutoSwap(
	sdkCtx sdk.Context,
	prc20AddressHex common.Address,
	ueaAddr common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	wpcAddr, err := k.GetUniversalCoreWPCAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	fee, err := k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
	if err != nil {
		return nil, err
	}

	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
}
```

**File:** x/uregistry/keeper/msg_server.go (L41-64)
```go
// AddChainConfig enables the addition of a new chain configuration - Admin restricted.
func (ms msgServer) AddChainConfig(ctx context.Context, msg *types.MsgAddChainConfig) (*types.MsgAddChainConfigResponse, error) {
	if msg.ChainConfig == nil {
		return nil, errors.Wrap(sdkErrors.ErrInvalidRequest, "chain_config is required")
	}
	ms.k.Logger().Info("msg add chain config received", "signer", msg.Signer, "chain", msg.ChainConfig.Chain)

	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.AddChainConfig(ctx, msg.ChainConfig)
	if err != nil {
		return nil, err
	}

	return &types.MsgAddChainConfigResponse{}, nil
}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L38-46)
```go
	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}

	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("execute payload rejected: chain inbound disabled", "chain", caip2Identifier)
		return fmt.Errorf("inbound is disabled for chain %s", caip2Identifier)
	}
```
