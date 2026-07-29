### Title
Attacker-controlled `RevertInstructions.FundRecipient` accepted without zero-address validation, permanently burning refunded/re-minted PRC20 funds — ([File: x/uexecutor/keeper/outbound.go], [File: x/uexecutor/keeper/build_revert_outbound.go], [File: x/uexecutor/types/inbound.go])

### Summary
The PoolTogether bug is a "reset-to-default via zero address" trap: the code treats an *empty*/default delegate as self, but an *explicit* `address(0)` bypasses the safe fallback and permanently moves funds to the burn address. Push Chain's inbound-revert / gas-refund path has the same shape: `RevertInstructions.FundRecipient` falls back to `Sender` only when the field is the **empty string**, but if it is an explicit zero-address string, that value is used verbatim as the mint/refund target and the funds are unrecoverable.

### Finding Description
`RevertInstructions.FundRecipient` is part of the `Inbound` struct that is derived from the source-chain deposit event and is effectively attacker/user-controlled input (set by whoever calls the gateway on the source chain) [1](#0-0) . `Inbound.Canonicalize` only normalizes the address format of `FundRecipient` — it does not reject the zero address [2](#0-1) . Neither `ValidateBasic` nor `ValidateForExecution` inspects `RevertInstructions.FundRecipient` at all [3](#0-2) .

Every place that computes a revert/refund recipient uses the same unguarded fallback pattern: use `FundRecipient` if it is a non-empty string, otherwise fall back to `Sender`:

- `buildRevertOutbound` (used for INBOUND_REVERT paths and by `RevertStuckInbound`): [4](#0-3) 

- `handleFailedOutbound` (re-mints bridged PRC20 tokens back to the "revert recipient" when an outbound fails): [5](#0-4) 

- `applyGasRefund` (refunds excess gas fee, with or without a swap leg, to the "refund recipient"): [6](#0-5) 

In all three, if a user sets `FundRecipient` to the literal zero address string (`"0x0000000000000000000000000000000000000000"`, matching `EvmZeroAddress` defined in the same package [7](#0-6) ), the condition `FundRecipient != ""` is true, so the zero-address string is used as `recipient`/`refundRecipient` instead of falling back to `Sender`. The resulting `common.HexToAddress(...)` call converts it to the actual zero EVM address, and `CallPRC20Deposit` mints PRC20 tokens directly to that address via `depositPRC20Token` on `UNIVERSAL_CORE` [8](#0-7) , or `CallUniversalCoreRefundUnusedGas` sends the refunded gas token there. Whether the underlying PRC20/`UniversalCore` Solidity contract reverts a mint-to-zero is external to this repo and not verifiable from the Go code alone — if it does not revert, the tokens are burned irrecoverably; if it does revert, the outbound instead falls into `AbortOutbound` (manual-intervention state), which is itself a denial-of-service on the user's own refund.

### Impact Explanation
This mirrors the "resetting delegation loses funds forever" bug class exactly: the protocol has a legitimate "reset to default" input space (empty `FundRecipient` → sender) but a nearby, syntactically valid value (explicit zero address) escapes the safe default and is used as a literal fund destination with no additional guard. A user who is bridging funds into Push Chain and, out of confusion or a client/library bug, submits `fund_recipient = 0x000...000` (intending "no override"/default) will have their own inbound refund or gas-refund permanently minted to the zero address on any code path that reverts or refunds (chain disabled, outbound failure, excess gas refund, `RevertStuckInbound` admin path, etc.). This is a "permanent loss of user-controlled funds" scenario per the allowed-impact scope, reachable purely through the normal, unprivileged inbound submission flow with no validator or admin misbehavior required.

### Likelihood Explanation
Likelihood is moderate: it requires the field to literally be the zero address, which is an edge case rather than something an attacker gains from stealing others' funds (it primarily harms the same account that set the field, similar to the original PoolTogether report). However, because `FundRecipient` flows through unauthenticated, user/attacker-supplied event data with no validation anywhere in the pipeline (`Canonicalize`, `ValidateBasic`, `ValidateForExecution` all skip it), any client, wallet, or relayer bug that defaults an unset field to `0x0` instead of omitting it will silently trigger this, and there is no on-chain safety net to catch it before the mint/refund executes.

### Recommendation
Add an explicit zero-address (and any other clearly-invalid sentinel) check for `RevertInstructions.FundRecipient` at the same point defaults are already applied:
- In `Inbound.ValidateForExecution` (and/or `Canonicalize`), treat a zero-address `FundRecipient` the same as an empty one, so it always falls back to `Sender`.
- Apply the same normalization inside `buildRevertOutbound`, `handleFailedOutbound`, and `applyGasRefund` (or better, centralize the fallback logic in one helper, e.g. `RevertInstructions.ResolveRecipient(sender)`), so all three call sites cannot diverge again in the future.
- Reject/normalize the zero address before calling `CallPRC20Deposit` / `CallUniversalCoreRefundUnusedGas` as a defense-in-depth backstop even if upstream validation is bypassed.

### Proof of Concept
1. User initiates an inbound deposit on a source chain, setting the gateway event's `revert_instructions.fund_recipient` to `0x0000000000000000000000000000000000000000` (e.g., due to a wallet/SDK bug that zero-fills unset struct fields instead of omitting them).
2. Universal Validators observe and vote the inbound; `Inbound.Canonicalize` normalizes the address format but does not clear/reject the zero address [2](#0-1) .
3. Execution later fails for a legitimate reason (chain disabled, swap failure, outbound failure, or the admin runs `RevertStuckInbound` for an expired ballot).
4. `buildRevertOutbound` computes `recipient := inbound.Sender` then overwrites it because `RevertInstructions.FundRecipient != ""` is true, setting `recipient = "0x000...000"` [4](#0-3) .
5. The revert outbound (or, on the funds-re-mint path, `handleFailedOutbound`) calls `CallPRC20Deposit`/mints to `common.HexToAddress("0x0000000000000000000000000000000000000000")` [5](#0-4) , permanently losing the user's bridged funds if the PRC20 contract does not itself guard against minting to `address(0)` (this final external-contract behavior could not be confirmed from this repository alone).

### Citations

**File:** proto/uexecutor/v1/types.proto (L95-100)
```text
message RevertInstructions {
  option (amino.name) = "uexecutor/revert_instructions";
  option (gogoproto.equal) = true;

  string fund_recipient = 1;       // where funds go in revert/refund
}
```

**File:** x/uexecutor/types/inbound.go (L14-14)
```go
const EvmZeroAddress = "0x0000000000000000000000000000000000000000"
```

**File:** x/uexecutor/types/inbound.go (L32-36)
```go
	if p.RevertInstructions != nil {
		// Refunds return to the source chain.
		p.RevertInstructions.FundRecipient = utils.LenientCanonicalizeAddress(p.SourceChain, p.RevertInstructions.FundRecipient)
	}
}
```

**File:** x/uexecutor/types/inbound.go (L90-175)
```go
func (p Inbound) ValidateBasic() error {
	// Validate source_chain (must follow CAIP-2 format) — needed for UTX key
	chain := strings.TrimSpace(p.SourceChain)
	if chain == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "source chain cannot be empty")
	}
	if !strings.Contains(chain, ":") {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "source chain must be in CAIP-2 format <namespace>:<reference>")
	}

	// Validate tx_hash — needed for UTX key
	if strings.TrimSpace(p.TxHash) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "tx_hash cannot be empty")
	}

	// Validate sender — needed for revert recipient fallback
	if strings.TrimSpace(p.Sender) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "sender cannot be empty")
	}

	// Validate log_index — needed for UTX key
	if strings.TrimSpace(p.LogIndex) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "log_index cannot be empty")
	}

	// Validate tx_type enum — needed to route execution
	if _, ok := TxType_name[int32(p.TxType)]; !ok || p.TxType == TxType_UNSPECIFIED_TX {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid tx_type: %v", p.TxType)
	}

	return nil
}

// ValidateForExecution checks fields that are required for actual execution of the inbound.
// Called after ballot finalization, before ExecuteInbound. Failures here produce a failed
// PCTx and (for non-isCEA) a revert outbound, rather than dropping the vote.
func (p Inbound) ValidateForExecution() error {
	// Validate amount as uint256
	if strings.TrimSpace(p.Amount) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty")
	}
	bi, ok := new(big.Int).SetString(p.Amount, 10)
	if !ok || bi.Sign() < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid non-negative uint256")
	}
	// Only GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD allow zero amount (skip deposit, still execute payload)
	if bi.Sign() == 0 && p.TxType != TxType_GAS_AND_PAYLOAD && p.TxType != TxType_FUNDS_AND_PAYLOAD {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be positive for this tx type")
	}

	// Validate asset_addr
	if strings.TrimSpace(p.AssetAddr) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "asset_addr cannot be empty")
	}

	// isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD
	if p.IsCEA && p.TxType != TxType_FUNDS && p.TxType != TxType_FUNDS_AND_PAYLOAD && p.TxType != TxType_GAS_AND_PAYLOAD {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD tx types, got: %v", p.TxType)
	}

	// Validate fields required per tx_type
	switch p.TxType {
	case TxType_FUNDS_AND_PAYLOAD, TxType_GAS_AND_PAYLOAD:
		if p.UniversalPayload == nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "payload is required for payload tx types")
		}
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
		if err := p.UniversalPayload.ValidateBasic(); err != nil {
			return errors.Wrap(err, "invalid payload")
		}
	case TxType_FUNDS, TxType_GAS:
		if strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
		}
		if !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address: %s", p.Recipient)
		}
	}

	return nil
}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-14)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}
```

**File:** x/uexecutor/keeper/outbound.go (L107-119)
```go
		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)
```

**File:** x/uexecutor/keeper/outbound.go (L201-206)
```go
	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
```

**File:** x/uexecutor/keeper/evm.go (L262-303)
```go
func (k Keeper) CallPRC20Deposit(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // sender: module account
		handlerAddr,        // destination
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20Token",
		prc20Address,
		amount,
		to,
	)
}
```
