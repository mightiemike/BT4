Confirmed: `depositPRC20` in `x/uexecutor/keeper/handler.go` fetches the `TokenConfig` via `k.uregistryKeeper.GetTokenConfig` and only checks `tokenConfig.NativeRepresentation == nil` before proceeding to mint PRC20. It never checks the `TokenConfig.Enabled` field, even though that field is explicitly documented as "Whether this token is enabled for minting/bridging".

### Title
Missing `TokenConfig.Enabled` validation allows minting/bridging of disabled tokens - (File: x/uexecutor/keeper/handler.go)

### Summary
`x/uregistry` defines a per-token `Enabled` flag [1](#0-0)  intended to gate whether a token can be minted/bridged. While `x/uregistry` enforces the analogous chain-level `ChainEnabled.IsInboundEnabled`/`IsOutboundEnabled` flags at multiple points in the inbound/outbound flow [2](#0-1) [3](#0-2) , no equivalent check exists for `TokenConfig.Enabled` anywhere in the PRC20 deposit/mint path.

### Finding Description
The `depositPRC20` helper, called from the inbound-execution flow (`ExecuteInboundFundsAndPayload`) for every inbound funds/payload/CEA path, fetches the token config solely to read the `NativeRepresentation.ContractAddress`, and only errors out if `NativeRepresentation` is nil: [4](#0-3) . It never inspects `tokenConfig.Enabled`. Consequently, once a token is registered via `AddTokenConfig` [5](#0-4)  and an admin later disables it via `MsgUpdateTokenConfig` (setting `Enabled=false` to halt further minting/bridging of a compromised or deprecated asset), the flag has zero enforcement effect at runtime: `VoteInbound` only checks *chain*-level `IsChainInboundEnabled` [6](#0-5) , not the token itself, and `depositPRC20` proceeds to mint PRC20 for any token address present in `TokenConfigs` regardless of `Enabled`. A grep across the entire repository confirms `TokenConfig.Enabled`/`tokenConfig.Enabled` is never read outside of tests and proto-generated getters.

This is the direct native analog of the GMX finding: `SwapUtils`/`AdlUtils` failed to call `MarketUtils.getEnabledMarket` before acting on a market, allowing activity on a disabled market; here, `depositPRC20`/`ExecuteInboundFundsAndPayload` fail to call an equivalent `GetEnabledTokenConfig` before minting, allowing bridging of a disabled token.

### Impact Explanation
An unprivileged external attacker who observes (or triggers) an inbound event for a token address that the admin has disabled (e.g., due to a discovered vulnerability, price-manipulation, depeg, or liquidity-cap breach on the source chain token) can still have Universal Validators vote the inbound and have the core validator mint the corresponding PRC20 and execute payloads against it, since neither `VoteInbound` nor the execution path re-checks `TokenConfig.Enabled`. This directly undermines the registry's asset-safety control, allowing unauthorized mint of PRC20 for a token the protocol has explicitly decided to stop supporting — a corruption of PRC20 accounting semantics and unauthorized module-originated EVM execution (`DerivedEVMCall` minting) reachable from ordinary attacker-submitted/observed inbound events.

### Likelihood Explanation
High: exploitation requires no privileged access — any external actor whose funds/tx on the source chain match an already-registered (but disabled) token address will have their inbound naturally processed by honest Universal Validators and the core validator, since the enabled check is simply absent from the code path, not merely misconfigured.

### Recommendation
Add an `Enabled` check analogous to the existing `IsChainInboundEnabled`/`IsChainOutboundEnabled` guards, e.g., a `GetEnabledTokenConfig` (or `IsTokenEnabled`) helper in `x/uregistry/keeper/keeper.go`, and call it from `depositPRC20` (`x/uexecutor/keeper/handler.go`) and any other mint/swap/refund entry point that resolves a `TokenConfig`, rejecting execution before any EVM state change if `tokenConfig.Enabled == false`.

### Proof of Concept
1. Admin registers `TokenConfig{Chain: "eip155:X", Address: tokenAddr, Enabled: true, ...}` via `AddTokenConfig`.
2. Admin later disables it via `MsgUpdateTokenConfig{TokenConfig: {..., Enabled: false}}` (no code path removes the `TokenConfigs` entry or blocks subsequent lookups).
3. Attacker deposits `tokenAddr` on the source-chain gateway; Universal Validators observe and vote via `MsgVoteInbound`. `VoteInbound` only checks `IsChainInboundEnabled(sourceChain)` [2](#0-1) , which is unrelated to the token flag.
4. On quorum, `ExecuteInboundFundsAndPayload` calls `k.depositPRC20(...)` [7](#0-6) , which fetches `tokenConfig` and mints PRC20 via `CallPRC20Deposit` without ever checking `tokenConfig.Enabled` [4](#0-3) .
5. PRC20 is minted to the recipient/UEA despite the token being administratively disabled.

Note: I could not find any later-stage guard (e.g., in `CallPRC20Deposit`, `CallPRC20DepositAutoSwap`, or the `UniversalCore` Solidity contract) that re-validates token enablement; if such a check exists in the EVM-side `UniversalCore`/PRC20 contracts (outside this Go-scoped index), it would mitigate this at the contract level, but no reference to it appears in the indexed Go keeper code.

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

**File:** x/uregistry/keeper/msg_add_token_config.go (L10-36)
```go
// AddTokenConfig adds a new token configuration to the uregistry.
func (k Keeper) AddTokenConfig(ctx context.Context, tokenConfig *types.TokenConfig) error {
	// Ensure the chain exists
	if _, err := k.GetChainConfig(ctx, tokenConfig.Chain); err != nil {
		return fmt.Errorf("chain %s is not supported: %w", tokenConfig.Chain, err)
	}

	// More efficient check for existing token config
	storageKey := types.GetTokenConfigsStorageKey(tokenConfig.Chain, tokenConfig.Address)
	has, err := k.TokenConfigs.Has(ctx, storageKey)
	if err != nil {
		return err
	}
	if has {
		return fmt.Errorf("token config for %s on chain %s already exists", tokenConfig.Address, tokenConfig.Chain)
	}

	// Set the new token config
	if err := k.TokenConfigs.Set(ctx, storageKey, *tokenConfig); err != nil {
		return err
	}
	k.Logger().Info("token config added",
		"chain", tokenConfig.Chain,
		"token_address", tokenConfig.Address,
	)
	return nil
}
```

**File:** x/uregistry/keeper/keeper.go (L195-209)
```go
// IsChainInboundEnabled checks if inbound is enabled for a given chain
func (k Keeper) IsChainInboundEnabled(ctx context.Context, chain string) (bool, error) {
	config, err := k.GetChainConfig(ctx, chain)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			// chain not found
			return false, nil
		}
		return false, err
	}
	if config.Enabled == nil {
		return false, nil
	}
	return config.Enabled.IsInboundEnabled, nil
}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L69-80)
```go
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
```
