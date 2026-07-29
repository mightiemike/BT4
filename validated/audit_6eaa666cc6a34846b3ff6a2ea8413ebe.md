### Title
Admin-disabled `TokenConfig.Enabled` flag is never checked in inbound/outbound execution, allowing deposits and withdrawals of de-listed tokens - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/create_outbound.go`)

### Summary
`uregistry.TokenConfig` has an `Enabled` boolean field, settable via `MsgUpdateTokenConfig` (admin-only), whose documented purpose is to allow/de-list a specific token on a chain [1](#0-0) . Unlike `ChainConfig.Enabled` (`IsInboundEnabled`/`IsOutboundEnabled`), which is explicitly checked in `VoteInbound` and `BuildOutboundsFromReceipt` before any state mutation [2](#0-1) [3](#0-2) , the `TokenConfig.Enabled` flag is never read anywhere in `x/uexecutor`'s execution paths. `GetTokenConfig`/`GetTokenConfigByPRC20` are called purely to resolve PRC20 addresses/native representation, with no gate on `Enabled` in `ExecuteInboundFundsAndPayload` (uses `k.uregistryKeeper.GetTokenConfig` and immediately proceeds to deposit) [4](#0-3)  or in `BuildOutboundsFromReceipt` (uses `k.uregistryKeeper.GetTokenConfigByPRC20` and immediately builds the outbound) [5](#0-4) .

### Finding Description
The bug class from the report — a struct-level "enabled" flag that governs whether an operation is allowed, but which is set/toggled in one code path (admin update) and never checked in the consuming code path (execution) — has a direct analog in Push Chain's token registry.

- `x/uregistry/keeper/msg_update_token_config.go` / `msg_server.go` lets the admin flip `TokenConfig.Enabled` to `false` on an already-whitelisted token (e.g., to freeze/de-list a compromised or deprecated asset) [6](#0-5) .
- Deposit-side inbound execution in `ExecuteInboundFundsAndPayload` calls `k.depositPRC20` for the token identified by `utx.InboundTx.AssetAddr` without ever consulting `TokenConfig.Enabled`; the only registry lookup gating logic present is the chain-level `IsChainInboundEnabled` check performed earlier in `VoteInbound`, which is chain-scoped, not token-scoped [2](#0-1) .
- Outbound-side, `BuildOutboundsFromReceipt` checks `IsChainOutboundEnabled` for the destination chain but resolves the token via `GetTokenConfigByPRC20` with no `Enabled` check before minting/queuing the withdrawal outbound [7](#0-6) .
- The token registry README documents `Enabled`-like semantics only at the chain level for inbound/outbound, and lists `TokenConfig.Enabled` as part of the schema with no described enforcement point in `x/uexecutor` [8](#0-7) .

As a result, once the admin disables a token via `MsgUpdateTokenConfig{Enabled:false}` — intended to stop it from being deposited/withdrawn (e.g., during an incident, oracle compromise, or liquidity-cap breach) — the token remains fully usable end-to-end: users can still deposit it inbound (PRC20 minted to their UEA) and withdraw it outbound (burned and released on the source chain), because neither `ExecuteInboundFundsAndPayload` nor `BuildOutboundsFromReceipt`/`attachOutboundsToUtx` ever queries the flag.

### Impact Explanation
This maps to "corruption of PRC20 or native asset accounting … token mapping" invariant in the allowed-impact gate: the protocol's own token-level admission control is silently inert. The practical severity depends on why a token was disabled — if disabled because of a liquidity-cap or risk issue, continued use could let an unprivileged user mint/move PRC20 supply or drain a de-listed asset's escrowed liquidity that admins believed was frozen. Per the impact gate's own framing (mirroring the source report), this is best characterized as Medium: the disabled-token flow still behaves like a normal, correctly-accounted flow — the deposit/withdraw math itself isn't broken — but the control that is supposed to prevent using it never fires, defeating governance/admin risk-mitigation intent for token operations.

### Likelihood Explanation
High. No privileged or adversarial-node assumption is needed — any ordinary user submitting a normal cross-chain deposit or triggering a normal outbound for an "already disabled" token will succeed, because the disabling functionality has zero enforcement points in the execution keeper. This is deterministic and always reproducible once a token's `Enabled` is flipped to `false`.

### Recommendation
Add an explicit `TokenConfig.Enabled` check analogous to the existing chain-level checks:
- In `ExecuteInboundFundsAndPayload` (and/or earlier, in `VoteInbound`/`inbound.ValidateForExecution`), look up `k.uregistryKeeper.GetTokenConfig(ctx, sourceChain, assetAddr)` and reject/branch-to-revert if `!tokenConfig.Enabled` before calling `depositPRC20`.
- In `BuildOutboundsFromReceipt`, after resolving `tokenCfg` via `GetTokenConfigByPRC20`, reject the outbound (or route it to a revert/rescue path) if `!tokenCfg.Enabled`, mirroring the existing `IsChainOutboundEnabled` pattern at [3](#0-2) .

### Proof of Concept
1. Admin registers a chain and token via `MsgAddChainConfig` / `MsgAddTokenConfig` with `Enabled: true`, matching the setup used in `test/integration/uexecutor/inbound_synthetic_bridge_test.go` [9](#0-8) .
2. Admin later sends `MsgUpdateTokenConfig` for that same token with `Enabled: false`, which succeeds via `UpdateTokenConfig` with no downstream propagation checks [6](#0-5) .
3. A universal validator quorum submits `MsgVoteInbound` for a deposit of that (now-disabled) token from the source chain. `VoteInbound` only checks `IsChainInboundEnabled` (chain-level), which is still `true`, so the vote is accepted and finalized [2](#0-1) .
4. `ExecuteInboundFundsAndPayload` runs, looks up `GetTokenConfig` purely for its `NativeRepresentation.ContractAddress`, and calls `depositPRC20` — the PRC20 mint succeeds despite `Enabled == false` [4](#0-3) .
5. Symmetrically, an outbound withdrawal for the same disabled token proceeds through `BuildOutboundsFromReceipt`, which never inspects `tokenCfg.Enabled` before attaching the outbound for TSS signing [5](#0-4) .

Note: I was unable to fully verify whether any additional gating exists for `TokenConfig.Enabled` inside EVM-side precompiles/contracts (e.g., the PRC20 or gateway Solidity contracts) that may not be indexed in this repository's Go code; if such enforcement exists at the EVM layer, it would need to be confirmed with direct file access before treating this as fully unmitigated.

### Citations

**File:** test/integration/uexecutor/inbound_synthetic_bridge_test.go (L49-70)
```go

	prc20Address := utils.GetDefaultAddresses().PRC20USDCAddr
	testAddress := utils.GetDefaultAddresses().DefaultTestAddr
	usdcAddress := utils.GetDefaultAddresses().ExternalUSDCAddr

	tokenConfigTest := uregistrytypes.TokenConfig{
		Chain:        "eip155:11155111",
		Address:      usdcAddress.String(),
		Name:         "USD Coin",
		Symbol:       "USDC",
		Decimals:     6,
		Enabled:      true,
		LiquidityCap: "1000000000000000000000000",
		TokenType:    1,
		NativeRepresentation: &uregistrytypes.NativeRepresentation{
			Denom:           "",
			ContractAddress: prc20Address.String(),
		},
	}

	app.UregistryKeeper.AddChainConfig(ctx, &chainConfigTest)
	app.UregistryKeeper.AddTokenConfig(ctx, &tokenConfigTest)
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

**File:** x/uexecutor/keeper/create_outbound.go (L49-74)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}

		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L208-219)
```go
	// Smart contract path: call executeUniversalTx and return
	if isSmartContract {
		tokenConfig, tcErr := k.uregistryKeeper.GetTokenConfig(sdkCtx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)

		var contractReceipt *evmtypes.MsgEthereumTxResponse
		var contractErr error
		var feeErr error

		if tcErr != nil {
			contractErr = fmt.Errorf("token config lookup failed: %w", tcErr)
		} else {
			prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
```

**File:** x/uregistry/keeper/msg_server.go (L120-147)
```go
// UpdateTokenConfig implements types.MsgServer.
func (ms msgServer) UpdateTokenConfig(ctx context.Context, msg *types.MsgUpdateTokenConfig) (*types.MsgUpdateTokenConfigResponse, error) {
	if msg.TokenConfig == nil {
		return nil, errors.Wrap(sdkErrors.ErrInvalidRequest, "token_config is required")
	}
	ms.k.Logger().Info("msg update token config received",
		"signer", msg.Signer,
		"chain", msg.TokenConfig.Chain,
		"token_address", msg.TokenConfig.Address,
	)

	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.UpdateTokenConfig(ctx, msg.TokenConfig)
	if err != nil {
		return nil, err
	}

	return &types.MsgUpdateTokenConfigResponse{}, nil
}
```

**File:** x/uregistry/README.md (L7-18)
```markdown
- **Stores chain configs** — for each supported external chain (CAIP-2 keyed): public RPC URL, gateway contract address, gateway/vault method identifiers, block confirmation thresholds, gas oracle fetch interval, VM type, and inbound/outbound enabled flags.
- **Stores token configs** — per (chain, token address): symbol, decimals, native PRC20 representation, liquidity cap, ERC20/SPL/etc. type.
- **Deploys reserved system contracts** — on fresh genesis, deploys `UNIVERSAL_GATEWAY_PC` and reserved proxy slots into the EVM at deterministic addresses (`0x...C1`, `0x...B0`, `0x...B1`, `0x...B2`).
- **Exposes lookup helpers** for the rest of the codebase, including `GetTokenConfigByPRC20` (reverse lookup from a PRC20 contract address to its source-chain token).

## State (KV layout)

| Prefix | Collection | Type | Purpose |
|---|---|---|---|
| `0` | `Params` | `Item[Params]` | Module parameters (admin address) |
| `1` | `ChainConfigs` | `Map[string, ChainConfig]` | Per-CAIP-2 chain configuration |
| `2` | `TokenConfigs` | `Map[string, TokenConfig]` | Token configuration, keyed by `chain:address` |
```
