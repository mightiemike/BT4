### Title
`TokenConfig.Enabled` flag is ignored during PRC20 minting - (`File: x/uexecutor/keeper/handler.go`)

### Summary
`TokenConfig` carries an `Enabled` boolean that is documented as gating minting/bridging, but the inbound execution paths that mint PRC20 tokens never inspect it. A token can be disabled in registry config and still be minted when an inbound for it is finalized, breaking the protocol's own token allowlist invariant.

### Finding Description
`TokenConfig` defines `enabled` as "Whether this token is enabled for minting/bridging" [1](#0-0) . The central deposit helper `depositPRC20` in `x/uexecutor/keeper/handler.go` fetches the config and immediately proceeds to `CallPRC20Deposit` without checking `tokenConfig.Enabled` [2](#0-1) . All inbound fund paths flow through this helper or read `TokenConfig` directly and likewise skip the flag:

- `ExecuteInboundFunds` calls `depositPRC20` [3](#0-2) .
- `ExecuteInboundFundsAndPayload` calls `depositPRC20` in the UEA, EOA, and non-CEA branches [4](#0-3) [5](#0-4) [6](#0-5) .
- `ExecuteInboundGas` and `ExecuteInboundGasAndPayload` call `GetTokenConfig` directly and use `NativeRepresentation.ContractAddress` without an `Enabled` check [7](#0-6) [8](#0-7) .

`VoteInbound` only validates `IsChainInboundEnabled` before finalization; it never validates the per-token `Enabled` flag [9](#0-8) .

### Impact Explanation
This is the same bug class as the external report: an allowlist/enable flag exists but is not consulted in the execution path. When an admin disables a token (e.g., due to a security incident, depegging, or sunsetting), the protocol intends to stop minting its PRC20 representation. Because `Enabled` is ignored, any subsequent source-chain deposit event for that token that honest UVs vote through will still execute and increase the PRC20 supply. This corrupts PRC20 accounting and constitutes unauthorized minting relative to the canonical registry config, both of which are in-scope impacts.

### Likelihood Explanation
High. The attacker needs no Push Chain privileges. If a disabled token still has a live gateway contract on its source chain, the attacker simply deposits that token into the gateway. Honest UVs observe the real event, reach quorum on `MsgVoteInbound`, and the chain finalizes the inbound. The execution path retrieves the disabled `TokenConfig` and mints PRC20 anyway. The only precondition is that a `TokenConfig` entry exists for the token and has been set to `Enabled: false`.

### Recommendation
Add an explicit `Enabled` check in `depositPRC20` immediately after fetching `TokenConfig`. If `!tokenConfig.Enabled`, return an error and let the existing failure handling record a `FAILED` PCTx and create an `INBOUND_REVERT` outbound for non-isCEA inbounds, consistent with how missing token configs are already handled. Apply the same check to the direct `GetTokenConfig` callers in `ExecuteInboundGas` and `ExecuteInboundGasAndPayload`.

### Proof of Concept
1. Admin registers `TokenConfig{Chain: "eip155:11155111", Address: <USDC>, Enabled: true, ...}`.
2. Admin later updates the config to `Enabled: false` to pause bridging.
3. Attacker deposits USDC into the Sepolia gateway contract.
4. Honest UVs observe the `addFunds` event and submit `MsgVoteInbound`.
5. Quorum is reached; `VoteInbound` rejects only if `IsChainInboundEnabled` is false, which it is not.
6. `ExecuteInboundFunds` calls `depositPRC20`, which fetches the disabled `TokenConfig` and calls `CallPRC20Deposit`.
7. PRC20 USDC is minted to the recipient on Push Chain despite the token being disabled in registry config.

### Citations

**File:** proto/uregistry/v1/types.proto (L130-145)
```text
message TokenConfig {
  option (amino.name) = "uregistry/token_config";
  option (gogoproto.equal) = true;
  option (gogoproto.goproto_stringer) = false;

  string chain = 1;                        // Chain ID in CAIP-2 format (e.g., eip155:1
  string address = 2;                      // Token address on external chain
  string name = 3;                         // Full token name (e.g., USD Coin)
  string symbol = 4;                       // Ticker (e.g., USDC)
  uint32 decimals = 5;                     // Number of decimals (e.g., 6 or 18)
  bool enabled = 6;                        // Whether this token is enabled for minting/bridging
  string liquidity_cap = 7;                // max supply cap for this token (string big.Int format)
  TokenType token_type = 8;                // Type of the token (e.g., ERC20, ERC721, ERC1155)

  NativeRepresentation native_representation = 9; // Native representation on the chain
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L69-76)
```go
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L89-96)
```go
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L144-151)
```go
			if execErr == nil && inboundAmount.Sign() > 0 {
				receipt, err = k.depositPRC20(
					sdkCtx,
					utx.InboundTx.SourceChain,
					utx.InboundTx.AssetAddr,
					ueaAddr,
					utx.InboundTx.Amount,
				)
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L39-45)
```go
	// --- step 1: get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L47-53)
```go
	// --- Step 1: token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
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
