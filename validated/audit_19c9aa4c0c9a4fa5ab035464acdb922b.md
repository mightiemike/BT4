## Analog Found: Disabled `TokenConfig.Enabled` flag not checked before minting PRC20 on inbound execution

### Title
Disabled token config still usable to mint PRC20 via inbound deposit — (File: `x/uexecutor/keeper/handler.go`)

### Summary
The `uregistry` module's `TokenConfig` message carries an `Enabled` field, documented as "Whether this token is enabled for minting/bridging" [1](#0-0) . This mirrors exactly the PaprController collateral-allow pattern in the source report: an admin-controlled boolean meant to gate whether an asset can be used to create protocol-issued value. Just like PaprController checked `isAllowed` only in `_addCollateralToVault` but not in `_increaseDebt`, Push Chain checks the analogous chain-level `ChainEnabled.IsInboundEnabled` flag in `VoteInbound` [2](#0-1) , but the **token-level** `Enabled` flag is never read anywhere in the execution path that actually mints PRC20.

### Finding Description
`depositPRC20` — the single chokepoint used by every inbound-funds execution path (`ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, `ExecuteInboundGas`) — fetches the `TokenConfig` purely to read its `NativeRepresentation.ContractAddress`, and never inspects `tokenConfig.Enabled`: [3](#0-2) 

The same pattern repeats in the gas-abstraction swap path, which also calls `GetTokenConfig` and immediately uses `tokenConfig.NativeRepresentation.ContractAddress` without an `Enabled` check: [4](#0-3) 

A grep across `x/uexecutor` for any use of the `Enabled` field confirms only `ChainConfig.Enabled` (`IsInboundEnabled`/`IsOutboundEnabled`, chain-level) is consulted in production code (`msg_vote_inbound.go`, `create_outbound.go`, `msg_migrate_uea.go`, `msg_execute_payload.go`); the token-level `Enabled` flag from `TokenConfig` only appears in test fixtures, always hardcoded to `true` [5](#0-4) , never asserted against in any negative-path test.

`x/uregistry` exposes `MsgUpdateTokenConfig` specifically so the admin can flip a token's `Enabled` flag (e.g., to pull a compromised, deprecated, or liquidity-capped-out token from bridging) without touching the chain-level enabled flags [6](#0-5) . Because the executor never reads this flag, disabling a token has no effect on the mint path: any ordinary user who deposits that token on the source-chain gateway will still have it processed normally by honest Universal Validators through `VoteInbound` → ballot finalization → `ExecuteInboundFunds`/`ExecuteInboundGas` → `depositPRC20` → `CallPRC20Deposit`, minting PRC20 against a token the admin explicitly turned off.

### Impact Explanation
This is an unauthorized-mint / token-mapping-corruption bug matching the "Registry and accounting path" pivot: `token config, PRC20/native representation... must not misroute value or attach the wrong asset semantics.` Disabling a `TokenConfig` is the only documented admin lever for stopping a specific token's bridging (e.g., after discovering the source-chain token contract is compromised, has an inflation bug, or needs to be delisted for liquidity-cap reasons). Since the mint path silently ignores this control, PRC20s backed by a disallowed/untrusted external asset keep getting minted 1:1 with deposits, defeating the intended admin safety switch and potentially minting PRC20 supply the protocol no longer wants circulating (broken accounting invariant between "trusted, bridgeable tokens" and "actual PRC20 supply in circulation").

### Likelihood Explanation
High likelihood of reachability: no privileged or malicious actor is required. Any unprivileged external-chain user can deposit the disabled token into the gateway contract; honest Universal Validators observe and vote normally (`VoteInbound` only checks `IsChainInboundEnabled`, not the token's `Enabled` flag), and honest core execution mints the PRC20 via `depositPRC20`/`CallPRC20Deposit`. The only precondition is that the admin has disabled a `TokenConfig` at some point without also removing it (`MsgRemoveTokenConfig` would prevent this, but `MsgUpdateTokenConfig{Enabled:false}` is the documented, less destructive control and is expected to work).

### Recommendation
Add an `Enabled` check in `depositPRC20` (and any other place that resolves `NativeRepresentation.ContractAddress` from `TokenConfig` for minting/execution) immediately after `GetTokenConfig`, mirroring the existing `IsChainInboundEnabled` check pattern in `VoteInbound`:

```go
tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, sourceChain, assetAddr)
if err != nil {
    return nil, err
}
if !tokenConfig.Enabled {
    return nil, fmt.Errorf("token %s on chain %s is disabled", assetAddr, sourceChain)
}
```
The failure should route through the same FAILED-PCTx / revert-outbound bookkeeping already used for other `depositPRC20` failures, so disabled-token deposits fail safely and (for non-CEA inbounds) trigger the existing refund/revert flow instead of silently minting.

### Proof of Concept
1. Admin calls `MsgUpdateTokenConfig` to set `Enabled=false` for `(eip155:11155111, USDC_ADDRESS)`, leaving `ChainConfig.Enabled.IsInboundEnabled=true`.
2. An unprivileged user deposits USDC into the Sepolia gateway as normal (`TxType_FUNDS`).
3. Honest Universal Validators observe and vote `MsgVoteInbound` — passes because `IsChainInboundEnabled` is still `true` (token-level flag is never consulted).
4. Ballot finalizes → `ExecuteInboundFunds` → `depositPRC20` (`x/uexecutor/keeper/handler.go:12-46`) fetches the disabled `TokenConfig` but proceeds directly to `CallPRC20Deposit`.
5. PRC20 is minted to the recipient despite the token being explicitly disabled by the admin — same class of "disabled resource still usable to create protocol value" as the referenced PaprController M-02 finding.

### Citations

**File:** proto/uregistry/v1/types.proto (L130-141)
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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L39-54)
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
```

**File:** test/integration/uexecutor/chain_enabled_test.go (L59-72)
```go
	tokenConfig := uregistrytypes.TokenConfig{
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
```

**File:** x/uregistry/README.md (L36-47)
```markdown
## Messages (`MsgServer`)

| Message | Authority | Purpose |
|---|---|---|
| `MsgAddChainConfig` | admin (`params.Admin`) | Register a new external chain |
| `MsgUpdateChainConfig` | admin | Modify an existing chain config |
| `MsgAddTokenConfig` | admin | Whitelist a token on a chain |
| `MsgUpdateTokenConfig` | admin | Modify a token config |
| `MsgRemoveTokenConfig` | admin | Remove a token from the whitelist |
| `MsgUpdateParams` | gov | Rotate the admin or update other params |

There is no validator-vote path here — chain and token additions are intentionally admin-curated. The expected workflow is gov passes `MsgUpdateParams` to install an admin key, and the admin executes config changes day-to-day.
```
