Confirmed: `Enabled` is checked in `x/uexecutor/keeper/msg_vote_inbound.go` (chain-level `IsInboundEnabled`) and `msg_execute_payload.go`/`msg_migrate_uea.go`, but nowhere in `create_outbound.go`, `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, `build_revert_outbound.go`, or `handler.go` is `TokenConfig.Enabled` ever read — only `NativeRepresentation` is used.

### Title
`TokenConfig.Enabled` disable is not enforced in the deposit/mint flow, allowing continued PRC20 minting for disabled tokens - (File: x/uexecutor/keeper/handler.go)

### Summary
`x/uregistry`'s `TokenConfig` has an `Enabled` field intended as a per-token "whitelist/circuit-breaker" toggle (`bool enabled = 6; // Whether this token is enabled for minting/bridging`), settable via admin `MsgUpdateTokenConfig`. `x/uexecutor` reads `TokenConfig` via `GetTokenConfig` in several inbound-execution and outbound-creation code paths, but only consumes `NativeRepresentation.ContractAddress` — it never checks the `Enabled` flag before calling `CallPRC20Deposit`/`CallPRC20DepositAutoSwap`.

### Finding Description
`ChainConfig.Enabled` (`IsInboundEnabled`/`IsOutboundEnabled`) is correctly enforced at the vote/execution entry points: [1](#0-0) , and equivalently in `msg_migrate_uea.go` and `msg_vote_inbound.go`. However `TokenConfig.Enabled` — the analogous per-token circuit breaker documented in the proto as controlling "minting/bridging" — is defined here: [2](#0-1)  but is never read by any consumer.

The actual PRC20 minting path, `depositPRC20`, fetches the token config purely to resolve the PRC20 contract address and immediately calls the mint-equivalent `CallPRC20Deposit`, with no `Enabled` check at all: [3](#0-2) . This function (or the sibling `gasAndPayloadDepositAutoSwap`) is invoked from every inbound execution flow that mints tokens (`execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, `execute_inbound_funds_and_payload.go`) and from outbound creation/revert accounting (`create_outbound.go`, `build_revert_outbound.go`), none of which reference `tokenConfig.Enabled`.

Once a token is whitelisted (`Enabled: true`) and a user has already triggered inbound votes/UTX creation referencing that token, admin-toggling `TokenConfig.Enabled = false` via `MsgUpdateTokenConfig` has no effect on execution of already-voted/pending UniversalTx or any subsequent inbound relaying a disabled token address that still resolves through `GetTokenConfig` (the lookup succeeds regardless of `Enabled`, since `GetTokenConfig` performs no enabled-state filtering — it simply returns the stored record). Any inbound event referencing that (chain, token address) pair continues to mint the mapped PRC20 to the recipient, and outbound accounting continues to treat that token as valid for revert/refund flows.

### Impact Explanation
This breaks the intended per-token circuit breaker: admins disabling a compromised, deprecated, or over-issued token mapping cannot stop new PRC20 minting for that token through the inbound funds/gas/payload paths. Since PRC20 minting directly corresponds to native-asset accounting and liquidity backing on Push Chain, unauthorized continued minting after a disable action can misroute value, break the liquidity-cap invariant the field's own comment describes ("max supply cap"), and defeat the intended emergency-disable control — falling under "corruption of PRC20 or native asset accounting" and "unauthorized mint" in the allowed-impact scope.

### Likelihood Explanation
Moderate: it requires an admin to have previously enabled a token (normal operational state) and later disable it while an unprivileged relayer/validator set continues submitting honest inbound votes for that same (chain, tokenAddress) pair — a routine, unprivileged, honest-validator flow, not a privileged-actor exploit. No malicious validator, relayer, or admin behavior is needed to trigger the missing check; the disable action simply fails to have its intended effect.

### Recommendation
Enforce `tokenConfig.Enabled` at the point of consumption — inside `depositPRC20` (and the `gasAndPayloadDepositAutoSwap`/autoswap variants) immediately after `GetTokenConfig` succeeds, returning an error (e.g., `TokenNotEnabled`) before resolving `NativeRepresentation` or calling `CallPRC20Deposit`. This mirrors the `ChainConfig.Enabled` pattern already used in `msg_execute_payload.go` and ensures admins have a working per-token kill switch independent of the chain-level flag.

### Proof of Concept
1. Admin adds `TokenConfig{Chain: "eip155:X", Address: tokenAddr, Enabled: true, NativeRepresentation: {ContractAddress: prc20Addr}}`.
2. Validators vote a `FUNDS` inbound referencing `tokenAddr`; execution mints PRC20 via `depositPRC20` — succeeds as expected.
3. Admin calls `MsgUpdateTokenConfig` setting `Enabled: false` to halt further bridging of this token.
4. A new (or already-pending) inbound event referencing the same `tokenAddr` is voted by honest validators and reaches execution; `GetTokenConfig` still returns the record (lookup is unconditional), `depositPRC20` still resolves `NativeRepresentation.ContractAddress` and calls `CallPRC20Deposit`, minting PRC20 to the recipient — the disable had no effect, contradicting the field's documented "enabled for minting/bridging" semantics [4](#0-3) .

### Citations

**File:** x/uexecutor/keeper/msg_execute_payload.go (L43-46)
```go
	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("execute payload rejected: chain inbound disabled", "chain", caip2Identifier)
		return fmt.Errorf("inbound is disabled for chain %s", caip2Identifier)
	}
```

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
