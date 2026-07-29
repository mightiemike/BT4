Confirmed: `depositPRC20` in `x/uexecutor/keeper/handler.go` fetches a `TokenConfig` and only checks that `NativeRepresentation != nil` — it never checks the `TokenConfig.Enabled` flag before minting PRC20 into the recipient's UEA/EOA/contract. This is a direct analog to the zBanc report's "isActive should only be returned if fully configured" pattern: the code treats a *configured* record (has a `NativeRepresentation`) as equivalent to an *active/enabled* one, when the protocol explicitly models these as separate states (`enabled` is documented as "Whether this token is enabled for minting/bridging" in `proto/uregistry/v1/types.proto:140`).

### Title
Disabled TokenConfig assets are still minted via `depositPRC20`, bypassing the `enabled` gate - (File: x/uexecutor/keeper/handler.go)

### Summary
`TokenConfig.Enabled` is the on-chain "is this asset allowed to move value" flag [1](#0-0) , analogous to the converter `isActive` gate in the zBanc report that must reflect full configuration/safety before the component is usable. `depositPRC20`, which is the sole gate the executor module uses before minting PRC20 for any inbound funds/gas/CEA path, never reads this flag — it only requires `NativeRepresentation != nil` [2](#0-1) .

### Finding Description
Every inbound execution path that mints PRC20 (`ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, `ExecuteInboundGas`, `ExecuteInboundGasAndPayload`, and their CEA variants) ultimately calls `k.depositPRC20(...)` [3](#0-2) [4](#0-3) . `depositPRC20` looks up the `TokenConfig` via `GetTokenConfig(ctx, sourceChain, assetAddr)` and only validates that `NativeRepresentation` is non-nil before calling `CallPRC20Deposit`, which performs an actual mint into the target address [5](#0-4) . The `Enabled` boolean on `TokenConfig` — explicitly documented as controlling whether the token is allowed for "minting/bridging" — is never read anywhere in the executor's inbound processing keeper package (confirmed by searching all `.Enabled` usages in `x/uexecutor/keeper/*.go`, none of which reference `TokenConfig.Enabled`; the only `.Enabled` hits are for `ChainConfig.Enabled`, a different field gating chain-level inbound/outbound, not per-asset). This is the same class of bug as the report: a record is treated as "ready for use" the moment its required struct fields (`NativeRepresentation`) are populated, without checking the explicit boolean meant to gate its live/active status.

### Impact Explanation
If a `TokenConfig` is registered (has `NativeRepresentation` populated) but subsequently marked `Enabled = false` — e.g., an admin pausing an asset due to a bridge exploit, a liquidity cap breach, or an unvetted mapping added before go-live — inbound votes that reference that asset will still successfully mint PRC20 to the recipient. This is unauthorized mint of PRC20 tokens against a record the protocol has flagged as not eligible for minting, corrupting PRC20 accounting and potentially allowing value creation/backing mismatches against the disabled asset (e.g., minting the wrapped token while the corresponding external vault/gateway for that asset is deliberately halted). This falls squarely under "corruption of PRC20 or native asset accounting" and "unauthorized mint" in the allowed impact list.

### Likelihood Explanation
The trigger is a completely ordinary, unprivileged inbound deposit: any external-chain transaction that Universal Validators observe and vote on via `MsgVoteInbound`/`MsgVoteOutbound`-style flows for `FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS`, `GAS_AND_PAYLOAD`, or CEA variants referencing a `sourceChain`/`assetAddr` pair whose `TokenConfig.Enabled` is `false`. No validator or admin dishonesty is required — honest UVs vote normally, and the missing check is purely in the executor keeper's deposit path. The only precondition is that such a disabled-but-still-populated `TokenConfig` exists, which is a realistic operational state (assets get disabled for many legitimate reasons after initial registration).

### Recommendation
Add an explicit check in `depositPRC20` (and any other minting entry point) that `tokenConfig.Enabled == true` before calling `CallPRC20Deposit`, mirroring how `ChainConfig.Enabled.IsInboundEnabled`/`IsOutboundEnabled` are already checked at the chain level (e.g., in `ExecutePayload` at `x/uexecutor/keeper/msg_execute_payload.go:43-46`). If disabled, the inbound should fail cleanly (producing a FAILED `PCTx` and, where applicable, an `INBOUND_REVERT` outbound), consistent with how missing token configs are already handled in existing tests.

### Proof of Concept
1. Admin (non-attacker, prior state) registers a `TokenConfig` for `eip155:11155111` / some `assetAddr` with a valid `NativeRepresentation.ContractAddress` (a deployed PRC20), then later sets `Enabled = false` on that config (e.g., via `UpdateTokenConfig`) to pause it.
2. An unprivileged external user sends a deposit to the source-chain gateway for that exact `assetAddr`.
3. Universal Validators observe and vote `MsgVoteInbound` for a `TxType_FUNDS` (or `FUNDS_AND_PAYLOAD`/`GAS`/`GAS_AND_PAYLOAD`) inbound referencing that `sourceChain`/`assetAddr`.
4. `ExecuteInboundFunds` → `depositPRC20` runs; since `NativeRepresentation != nil`, `CallPRC20Deposit` is invoked and PRC20 is minted to the recipient's UEA/address — despite `TokenConfig.Enabled == false`.
5. Confirm via `PCTx.Status == "SUCCESS"` in the resulting `UniversalTx` and a non-zero PRC20 `balanceOf(recipient)`, analogous to the existing test pattern in `test/integration/uexecutor/execute_inbound_gas_test.go:366-403` (which only covers the *missing* token-config case, not the *disabled* one).

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
