### Title
Disabled token configs are still honored for PRC20 minting — `Enabled` flag on `TokenConfig` is never checked in the inbound execution path (File: `x/uexecutor/keeper/handler.go`)

### Summary
The external report flags `buyCredits` for minting tokens against any arbitrary ERC20 without verifying legitimacy via an admin-curated allowlist. Push Chain does maintain an admin-curated allowlist analog — `uregistry.TokenConfig`, which includes an explicit `Enabled` bool documented as "Whether this token is enabled for minting/bridging" [1](#0-0) . However, every inbound execution path that mints PRC20 against a `TokenConfig` only checks that the config *exists*, never that `Enabled == true`.

### Finding Description
`depositPRC20`, the function that mints PRC20 tokens to a recipient for every inbound (`FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS`, `GAS_AND_PAYLOAD`) flow, calls `GetTokenConfig` and only checks the returned `error` and whether `NativeRepresentation` is set — it never inspects `tokenConfig.Enabled`: [2](#0-1) 

The same pattern repeats in `ExecuteInboundGas` (uses `tokenConfig.NativeRepresentation.ContractAddress` directly after only checking the lookup `err`) [3](#0-2)  and `ExecuteInboundGasAndPayload` [4](#0-3) , and in `ExecuteInboundFundsAndPayload`'s deposit calls [5](#0-4) .

The admin tooling clearly intends `Enabled=false` to mean "not usable for minting/bridging" — `MsgUpdateTokenConfig` lets the admin toggle it independently of `MsgRemoveTokenConfig` [6](#0-5) . But nowhere in `x/uexecutor`'s vote-inbound-to-execution pipeline is `Enabled` consulted; the config is treated as valid as long as it's present in the `TokenConfigs` map. This mirrors the exact bug-class from the report: the presence of a token in a registry (analogous to "any arbitrary ERC20") is treated as sufficient authorization to mint, without checking a legitimacy/status field the registry itself defines for that purpose.

### Impact Explanation
If an admin disables a token (e.g., after a depeg, an external-chain exploit, a bridge compromise, or a deprecated/rug-prone asset) via `MsgUpdateTokenConfig` (setting `Enabled=false`) rather than removing the config entirely, honest Universal Validators will still vote inbound observations referencing that `chain:address` to quorum (chain/token existence is only validated by CAIP-2 chain lookup and inbound structure, not the `Enabled` flag at the voting layer either), and `x/uexecutor`'s execution stage will still mint PRC20 to the recipient. This is an unauthorized-mint path: value backed by a token the protocol has explicitly flagged as unsafe/unsupported for minting continues to be created 1:1, corrupting PRC20 accounting and defeating the entire purpose of the `Enabled` gate. It matches the "unauthorized mint … or corruption of PRC20 … accounting" impact bucket.

### Likelihood Explanation
Reachable by any unprivileged external user: simply deposit the disabled asset into the already-configured gateway on the (still enabled) external chain using the same `chain:address` pair that remains in `TokenConfigs` with `Enabled=false`. No validator, relayer, or admin misbehavior is required — only that the admin used the "disable" lever instead of "remove," which is the documented, sanctioned way to pause a token (`MsgUpdateTokenConfig`). The current code makes that lever silently ineffective for minting.

### Recommendation
In `depositPRC20` (and equivalently in `ExecuteInboundGas` / `ExecuteInboundGasAndPayload` / `ExecuteInboundFundsAndPayload` wherever `GetTokenConfig` result is consumed for minting), explicitly reject execution when `tokenConfig.Enabled == false`, routing the inbound down the same failure/PCTx/revert path used for `GetTokenConfig` lookup errors today.

### Proof of Concept
1. Admin registers `TokenConfig{Chain: "eip155:X", Address: tokenA, Enabled: true, NativeRepresentation: {ContractAddress: prc20A}}`.
2. Later, admin calls `MsgUpdateTokenConfig` to set `Enabled=false` for `tokenA` (e.g., due to a discovered vulnerability in `tokenA`), while leaving the `ChainConfig` inbound-enabled.
3. Any user deposits `tokenA` into the external-chain gateway. Universal Validators observe and vote `MsgVoteInbound` to quorum (no `Enabled` check exists in the vote/finalize path).
4. `x/uexecutor` executes the inbound; `depositPRC20` → `GetTokenConfig` succeeds (config still exists, only `Enabled` is false) → `CallPRC20Deposit` mints `prc20A` to the recipient, despite the admin having disabled the token.

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

**File:** x/uexecutor/keeper/handler.go (L12-29)
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L68-80)
```go
				// UEA path: deposit PRC20 into the UEA (if amount > 0), then execute payload via UEA
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
