Confirmed: `TokenConfig.Enabled` exists as a data field, but none of the value-moving inbound execution paths (`depositPRC20` in `x/uexecutor/keeper/handler.go:12-46`, `ExecuteInboundFundsAndPayload` in `x/uexecutor/keeper/execute_inbound_funds_and_payload.go:210-256`, `ExecuteInboundGas` in `x/uexecutor/keeper/execute_inbound_gas.go:40-158`, `buildRevertOutbound` in `x/uexecutor/keeper/build_revert_outbound.go:28-48`) check `tokenConfig.Enabled` before minting/swapping/crediting PRC20. They only check `tokenConfig.NativeRepresentation != nil` and whether `GetTokenConfig` returns `ErrNotFound`.

### Title
Token-disable/removal via `uregistry` `Enabled` flag or `RemoveTokenConfig` does not stop already-registered PRC20 mint/swap flows because inbound execution never checks `TokenConfig.Enabled` - (File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/handler.go)

### Summary
The original report's root cause is that a marketplace has no way to react to a depegged/compromised whitelisted asset before value transfers execute against it. Push Chain's `uregistry` module *does* model this correctly at the config layer — `TokenConfig` has an `Enabled bool` field [1](#0-0)  and there is also a full `RemoveTokenConfig` message/keeper method [2](#0-1) . However, the actual value-moving code paths in `x/uexecutor` that consume `TokenConfig` for minting PRC20 or performing gas-abstraction swaps never read or enforce `TokenConfig.Enabled`.

### Finding Description
`depositPRC20` (called on every funded inbound) fetches the token config and only validates that `NativeRepresentation != nil`; it never checks `Enabled`: [3](#0-2) . `ExecuteInboundGas`, which performs the PRC20 deposit + auto-swap for gas abstraction, likewise only checks that `GetTokenConfig` succeeded (i.e., the row still exists) and proceeds to swap/mint regardless of the `Enabled` flag: [4](#0-3) . The revert-outbound builder similarly only checks for a missing `NativeRepresentation`, not `Enabled`: [5](#0-4) .

This means the admin's only real enforcement lever is `MsgRemoveTokenConfig`, which deletes the row entirely (verified by the integration test that shows `GetTokenConfig` failing after removal produces a FAILED PCTx and an `INBOUND_REVERT`) [6](#0-5) . Setting `Enabled=false` via `MsgUpdateTokenConfig` — the softer, presumably intended "pause without unregistering" mechanism analogous to a "depeg freeze" — is a complete no-op for already-pending or newly-arriving inbound funds transfers and gas-abstraction swaps, since nothing in `x/uexecutor` reads that field.

Per the scope's "Allowed Impact Gate," the actual admin actions (calling `RemoveTokenConfig`/`UpdateTokenConfig`) are privileged and out of scope. The reachable, unprivileged-attacker angle is narrower than the original report: an unprivileged relayer/attacker who observes that the admin has flipped `Enabled=false` on a token (intending to halt it, e.g., mid-depeg) can still submit/vote a `MsgVoteInbound` for that asset and it will be honored by honest validators and minted/swapped through `depositPRC20`/`ExecuteInboundGas`, because chain-level enable checks exist (`IsChainInboundEnabled`, checked in `x/uexecutor/keeper/msg_vote_inbound.go:31-39`) but the equivalent token-level check does not exist anywhere in the execution path.

### Impact Explanation
If `TokenConfig.Enabled=false` is used operationally as the intended "pause this asset" control (mirroring the recommended mitigation of "checking supported status before executing a sale"), it silently fails to stop new inbound mints/swaps for that PRC20. This can result in continued minting of a depegged/compromised asset's PRC20 representation on Push Chain after the admin believed it had frozen it, corrupting PRC20 accounting and gas-abstraction swap accounting for as long as the token row is merely disabled rather than fully removed. The impact is contained to whatever unprivileged users still submit inbound transactions for that specific disabled-but-not-removed asset.

### Likelihood Explanation
Requires only an ordinary, unprivileged user submitting an inbound funds/gas transaction for the affected asset while it is `Enabled=false` but not yet `Removed`. Given the module exposes `Enabled` as a distinct, less destructive alternative to `RemoveTokenConfig`, it is plausible for operators to use it for exactly this "temporarily halt while depeg is assessed" purpose. Likelihood is moderate — it depends on an admin using the softer flag under time pressure — but the gap is deterministic once the flag is set.

### Recommendation
Add an `Enabled` check to `GetTokenConfig` consumers in the inbound execution path (`depositPRC20`, `ExecuteInboundGas`, and `buildRevertOutbound`/any other places that read `TokenConfig` for minting or gas-fee lookups), returning a hard failure (triggering `INBOUND_REVERT`) when `tokenConfig.Enabled == false`, consistent with how `IsChainInboundEnabled` is already enforced in `VoteInbound`.

### Proof of Concept
1. Admin calls `MsgUpdateTokenConfig` to set `Enabled=false` on a `TokenConfig` (e.g., after detecting a depeg), without calling `MsgRemoveTokenConfig`.
2. An unprivileged user still submits a bridge/inbound transaction on the source chain moving that asset to the Push Chain gateway.
3. Universal Validators vote `MsgVoteInbound` (chain-level `IsChainInboundEnabled` passes since only chain enablement is checked in `x/uexecutor/keeper/msg_vote_inbound.go:31-39`).
4. `ExecuteInboundFundsAndPayload` → `depositPRC20` fetches the (disabled) `TokenConfig`, sees `NativeRepresentation != nil`, and proceeds to mint PRC20 to the recipient/UEA, exactly as if the token were still enabled — see `x/uexecutor/keeper/handler.go:12-46`. [3](#0-2) [4](#0-3) [7](#0-6) [2](#0-1)

### Citations

**File:** api/uregistry/v1/types.pulsar.go (L5710-5724)
```go
type TokenConfig struct {
	state         protoimpl.MessageState
	sizeCache     protoimpl.SizeCache
	unknownFields protoimpl.UnknownFields

	Chain                string                `protobuf:"bytes,1,opt,name=chain,proto3" json:"chain,omitempty"`                                                           // Chain ID in CAIP-2 format (e.g., eip155:1
	Address              string                `protobuf:"bytes,2,opt,name=address,proto3" json:"address,omitempty"`                                                       // Token address on external chain
	Name                 string                `protobuf:"bytes,3,opt,name=name,proto3" json:"name,omitempty"`                                                             // Full token name (e.g., USD Coin)
	Symbol               string                `protobuf:"bytes,4,opt,name=symbol,proto3" json:"symbol,omitempty"`                                                         // Ticker (e.g., USDC)
	Decimals             uint32                `protobuf:"varint,5,opt,name=decimals,proto3" json:"decimals,omitempty"`                                                    // Number of decimals (e.g., 6 or 18)
	Enabled              bool                  `protobuf:"varint,6,opt,name=enabled,proto3" json:"enabled,omitempty"`                                                      // Whether this token is enabled for minting/bridging
	LiquidityCap         string                `protobuf:"bytes,7,opt,name=liquidity_cap,json=liquidityCap,proto3" json:"liquidity_cap,omitempty"`                         // max supply cap for this token (string big.Int format)
	TokenType            TokenType             `protobuf:"varint,8,opt,name=token_type,json=tokenType,proto3,enum=uregistry.v1.TokenType" json:"token_type,omitempty"`     // Type of the token (e.g., ERC20, ERC721, ERC1155)
	NativeRepresentation *NativeRepresentation `protobuf:"bytes,9,opt,name=native_representation,json=nativeRepresentation,proto3" json:"native_representation,omitempty"` // Native representation on the chain
}
```

**File:** x/uregistry/keeper/msg_server.go (L149-172)
```go
// RemoveTokenConfig implements types.MsgServer.
func (ms msgServer) RemoveTokenConfig(ctx context.Context, msg *types.MsgRemoveTokenConfig) (*types.MsgRemoveTokenConfigResponse, error) {
	ms.k.Logger().Info("msg remove token config received",
		"signer", msg.Signer,
		"chain", msg.Chain,
		"token_address", msg.TokenAddress,
	)

	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.RemoveTokenConfig(ctx, msg.Chain, msg.TokenAddress)
	if err != nil {
		return nil, err
	}
	return &types.MsgRemoveTokenConfigResponse{}, nil
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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L40-54)
```go
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
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L28-36)
```go
	tokenCfg, err := k.uregistryKeeper.GetTokenConfig(sdkCtx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil || tokenCfg.NativeRepresentation == nil || tokenCfg.NativeRepresentation.ContractAddress == "" {
		k.Logger().Warn("failed to get PRC20 for revert outbound gas lookup, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"asset", inbound.AssetAddr,
			"error", err,
		)
		return outbound
	}
```

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L366-403)
```go
	t.Run("GAS inbound with missing token config records FAILED PCTx and creates revert", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals := setupInboundGasTest(t, 4)

		inbound.TxHash = "0xgas0020"

		// Remove token config to force GetTokenConfig to fail
		chainApp.UregistryKeeper.RemoveTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)

		reachGasQuorum(t, ctx, chainApp, vals, coreVals, inbound, 3)

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found, "universal tx should exist even when token config is missing")

		// Must have a FAILED PCTx
		require.NotEmpty(t, utx.PcTx, "PCTx entries must be recorded")
		hasFailed := false
		for _, pcTx := range utx.PcTx {
			if pcTx.Status == "FAILED" {
				hasFailed = true
				require.Contains(t, pcTx.ErrorMsg, "GetTokenConfig failed",
					"error message should indicate token config lookup failure")
				break
			}
		}
		require.True(t, hasFailed, "should have a FAILED PCTx when token config is missing")

		// Must have an INBOUND_REVERT outbound
		foundRevert := false
		for _, ob := range utx.OutboundTx {
			if ob.TxType == uexecutortypes.TxType_INBOUND_REVERT {
				foundRevert = true
				break
			}
		}
		require.True(t, foundRevert, "INBOUND_REVERT outbound should be created when token config is missing")
	})
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L31-39)
```go
	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}
```
