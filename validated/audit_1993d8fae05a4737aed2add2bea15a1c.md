This confirms `LiquidityCap` is never referenced anywhere in `x/uexecutor` (zero matches), meaning the entire inbound-deposit execution path — `depositPRC20`, `gasAndPayloadDepositAutoSwap`, `ExecuteInboundFundsAndPayload`, `ExecuteInboundGasAndPayload` — mints PRC20 tokens with no check against the configured cap.

### Title
Documented `TokenConfig.LiquidityCap` is never enforced, allowing unbounded PRC20 minting past the configured supply cap - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
The external report's root issue is that a documented protocol invariant (BeaconKit's `MaxEffectiveBalance` cap and minimum-balance gate) is not actually enforced in code, and a related hardcoded constant (`INITIAL_DEPOSIT`) silently diverges from the real enforced value, causing unexpected behavior. Push Chain has the same class of bug: `uregistry.TokenConfig.LiquidityCap` is documented in the proto as "max supply cap for this token" [1](#0-0)  and is required to be non-empty by `ValidateBasic` [2](#0-1) , but it is never read or compared against actual minted/deposited amounts anywhere in the inbound execution path in `x/uexecutor`.

### Finding Description
`TokenConfig` carries a `LiquidityCap` field whose documented purpose is to bound the total PRC20 supply that can be minted against a given external asset [3](#0-2) . Every inbound deposit path (`FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS_AND_PAYLOAD`) looks up the `TokenConfig` and calls `depositPRC20` / `gasAndPayloadDepositAutoSwap` directly with the attacker/user-controlled inbound `Amount`, with no comparison of cumulative minted supply against `LiquidityCap` at any point: [4](#0-3) [5](#0-4) 

A grep across the entire `x/uexecutor` package for `LiquidityCap` returns zero matches, confirming the field is write-only: it is validated for non-emptiness on `MsgAddTokenConfig`/`MsgUpdateTokenConfig` [6](#0-5)  but never read back by the executor when minting PRC20 tokens on inbound votes. Honest Universal Validators simply observe and vote real source-chain deposits, and the chain mints the corresponding PRC20 amount unconditionally once ballot quorum passes — there is no gate tying the amount to the registered cap.

This mirrors the external report precisely: a specification-level invariant ("EffectiveBalance capped... any balance in excess is automatically withdrawn") that is declared but not enforced by the implementation, creating a silent divergence between what operators/governance believe is guaranteed (a hard liquidity/supply ceiling per token) and what the code actually does (mint without limit).

### Impact Explanation
Medium-to-High: this does not require any privileged actor. Ordinary users depositing more of a source-chain asset than the registered `LiquidityCap` — of their own funds, through completely honest validators and honest ballot finalization — will have the chain mint corresponding PRC20 above the intended cap. This corrupts PRC20 accounting invariants the registry is supposed to guarantee (a `liquidity_cap` field exists specifically to bound the represented value on Push Chain per external asset), potentially over-collateralizing risk that downstream consumers (autoswap, gas abstraction, or accounting systems) assume is capped. It is an accounting-invariant violation, not a signature-verification bypass, so it falls under "corruption of PRC20 or native asset accounting" in the allowed impact list.

### Likelihood Explanation
High: no privileged action, no colluding validator, and no unusual conditions are required — a single legitimate large deposit through the normal inbound flow triggers it, since there is no code path that ever checks the field.

### Recommendation
1. Read the current cumulative minted/deposited amount for the token (tracked per `chain:address` or per PRC20 contract) at inbound-execution time and compare against `TokenConfig.LiquidityCap` before calling `depositPRC20`/`gasAndPayloadDepositAutoSwap`; reject (record `FAILED` PCTx + revert outbound) if the cap would be exceeded, consistent with how other `ValidateForExecution` failures are handled.
2. Alternatively, if `LiquidityCap` is intentionally unenforced/reserved for future use, remove it from `ValidateBasic`'s mandatory checks and clearly document it as informational only, to avoid operators believing it is an enforced safety control.
3. Add integration tests analogous to the existing zero-amount / stuck-inbound tests (`test/integration/uexecutor/inbound_zero_amount_test.go`, `revert_stuck_inbound_test.go`) that assert an inbound deposit exceeding `LiquidityCap` is rejected rather than executed.

### Proof of Concept
1. Register a `TokenConfig` for an external asset with `LiquidityCap: "1000000000000000000000000"` (as done in the test setups, e.g. `test/integration/uexecutor/vote_inbound_validation_test.go`).
2. Submit/observe (via honest UVs) an `Inbound` of `TxType_FUNDS` with `Amount` far exceeding the configured `LiquidityCap`.
3. Reach ballot quorum with `MsgVoteInbound` from honest validators as in `utils.ExecVoteInbound`.
4. `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` proceeds to call `depositPRC20`/`gasAndPayloadDepositAutoSwap` unconditionally, minting the full amount with `PcTx.Status == "SUCCESS"` — no check against `LiquidityCap` ever runs, confirmed by the absence of any `LiquidityCap` reference in `x/uexecutor`.

### Citations

**File:** proto/uregistry/v1/types.proto (L139-141)
```text
  uint32 decimals = 5;                     // Number of decimals (e.g., 6 or 18)
  bool enabled = 6;                        // Whether this token is enabled for minting/bridging
  string liquidity_cap = 7;                // max supply cap for this token (string big.Int format)
```

**File:** x/uregistry/types/token_config.go (L22-58)
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
```

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L67-80)
```go
			} else if isUEA {
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
