I've gathered enough evidence to confirm this analog. The key finding: `withdraw()` on the `UniversalGatewayPC` contract accepts an arbitrary `bytes to` parameter (`x/uexecutor/types/abi.go:679-687`), which is user-controlled and burned/pulled from `VaultPC` *before* the outbound is ever validated for address format. Once decoded into `event.Target` (`x/uexecutor/types/gateway_pc_event_decode.go:78`), it flows straight into `OutboundTx.Recipient` (`x/uexecutor/keeper/create_outbound.go:71`) with **no address-format validation** — `OutboundTx.ValidateBasic()` only checks it's non-empty (`x/uexecutor/types/outbound_tx.go:34-37`), unlike the strict hex-format checks applied to `Inbound.Recipient` in `ValidateForExecution` (`x/uexecutor/types/inbound.go:159-171`). If the destination-chain builder later fails to parse this recipient (e.g. SVM `PublicKeyFromBase58`/hex fallback failing, `universalClient/chains/svm/tx_builder.go:303-310`), the outbound stays permanently `PENDING` with the underlying asset already debited — there is no analog to `handleFailedOutbound`'s automatic re-mint, since that path is only reachable from a UV-voted `OutboundObservation` failure, not a build-time parse failure.

### Title
Unvalidated user-controlled outbound recipient permanently freezes bridged funds on build failure - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
`BuildOutboundsFromReceipt` copies the `Target` field straight from the user-emitted `UniversalTxOutbound` event into `OutboundTx.Recipient` without validating that it is a well-formed address for the destination chain's VM type. `OutboundTx.ValidateBasic()` only requires the field be non-empty. When the destination is an SVM chain and the user (accidentally or maliciously via a crafted `withdraw()`/CEA payload call) supplies bytes that are not a valid Solana public key (and not a 32-byte hex blob either), the universalClient's `TxBuilder.GetOutboundSigningRequest`/`BuildOutboundTransaction` fails to build a signable transaction. The outbound then remains stuck in `PENDING` forever: the automatic `handleFailedOutbound` re-mint/refund path is only triggered by a UV-voted `MsgVoteOutbound` observation of on-chain execution failure, which never happens because no transaction is ever built or broadcast to observe.

### Finding Description
`withdraw(bytes to, uint256 amount)` on `UniversalGatewayPC` (`x/uexecutor/types/abi.go:679-687`) is a permissionless, user-triggered function (called directly or via a `FUNDS_AND_PAYLOAD`/CEA universal payload) that burns/locks the PRC20 and emits `UniversalTxOutbound` with an arbitrary `bytes target` chosen entirely by the caller.

`DecodeUniversalTxOutboundFromLog` (`x/uexecutor/types/gateway_pc_event_decode.go:31-99`) decodes this into `event.Target` as a raw hex string with no chain-specific format check: [1](#0-0) 

`BuildOutboundsFromReceipt` then copies it verbatim into `OutboundTx.Recipient`: [2](#0-1) 

`OutboundTx.ValidateBasic()` — the only on-chain gate before the outbound enters `PendingOutbounds` and is picked up by universal validators — checks only that `Recipient` is non-empty, never that it is a valid address for `DestinationChain`'s VM type: [3](#0-2) 

Contrast this with `Inbound.ValidateForExecution`, which strictly enforces hex-address format for `Recipient` before execution: [4](#0-3) 

On the universalClient side, when the destination is SVM, `GetOutboundSigningRequest`/`BuildOutboundTransaction` attempt to parse `data.Recipient` as a base58 Solana pubkey, falling back to a 32-byte hex blob, and return a hard error if neither works: [5](#0-4) 

Because this failure occurs during **build**, not during on-chain execution, it never produces an `OutboundObservation` that could trigger `handleFailedOutbound`'s automatic re-mint/refund: [6](#0-5) 

That refund path is only reached via UV votes on a real destination-chain execution result (`MsgVoteOutbound`), so an outbound that can never be built or broadcast is stuck `PENDING` indefinitely with no automated recovery. The only remediation path found is the admin-only `RescueFundsOnSourceChain` flow (`x/uexecutor/keeper/create_outbound.go:187-263`), which requires a privileged/manual on-chain rescue call and is not automatic.

### Impact Explanation
An ordinary unprivileged user who supplies a malformed destination address when withdrawing to an SVM-family chain (either directly through `withdraw()`/`withdrawAndExecute()` or indirectly via a `FUNDS_AND_PAYLOAD`/CEA universal payload that calls the gateway on their behalf) causes their own already-debited/burned bridged asset to become permanently frozen in a `PENDING` outbound state that the protocol's automatic revert/refund machinery can never reach. This matches the in-scope "permanent freezing... of user or protocol-controlled funds" impact, mirroring the underlying BitVMBridge report's root cause (unvalidated destination-address input on a permissionless burn/withdraw path with no automatic recovery for the depositor).

### Likelihood Explanation
Likelihood is moderate: it requires the user (or a payload they invoke) to submit a malformed non-EVM address for an SVM-destination outbound, which can happen via wallet/integration bugs, off-chain address-format confusion between chains, or a maliciously crafted CEA/universal payload. Because `OutboundTx.ValidateBasic()` performs no chain-aware address check and the inconsistency with `Inbound`'s strict validation shows the gap is unintentional, this is readily triggerable without any privileged access, purely through the normal user-facing withdraw/outbound path.

### Recommendation
Validate `Recipient` against the destination chain's `VmType` (from `uregistry` `ChainConfig`) inside `OutboundTx.ValidateBasic()` or immediately in `BuildOutboundsFromReceipt`/`AttachRescueOutboundFromReceipt`, mirroring the strict format checks already used for `Inbound.Recipient`. If chain-aware validation at outbound-creation time is impractical, add an automatic path (analogous to `handleFailedOutbound`) that detects outbounds whose signing/broadcast can never succeed (e.g., after N failed build attempts or an expiry timer) and triggers the same PRC20 re-mint/refund logic without requiring manual admin rescue.

### Proof of Concept
1. Register an SVM `ChainConfig`/`TokenConfig` with outbound enabled.
2. From Push Chain, trigger `UniversalGatewayPC.withdraw(bytes to, uint256 amount)` (directly, or via a `FUNDS_AND_PAYLOAD`/CEA universal payload executed through the user's UEA) with `to` set to an arbitrary byte string that is neither a valid base58 Solana pubkey nor exactly 32 raw/hex bytes (e.g., 20-byte EVM-style address), targeting the SVM chain.
3. Observe `BuildOutboundsFromReceipt` (`x/uexecutor/keeper/create_outbound.go`) creates an `OutboundTx` with this malformed `Recipient`, passes `ValidateBasic()`, and is added to `PendingOutbounds` with `Status_PENDING`.
4. On the universalClient side, `TxBuilder.GetOutboundSigningRequest`/`BuildOutboundTransaction` for SVM (`universalClient/chains/svm/tx_builder.go`) fails with `"invalid recipient address format"` for every attempt, so no TSS signing session ever succeeds and no `OutboundObservation` is ever voted.
5. Confirm the outbound remains `Status_PENDING` indefinitely, with the underlying PRC20/asset already debited from the user and no automatic re-mint/refund triggered (unlike `handleFailedOutbound`, which only fires on a voted execution-failure observation).

### Citations

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L76-79)
```go
	event.ChainId = values[i].(string)
	i++
	event.Target = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
```

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}
```

**File:** x/uexecutor/types/outbound_tx.go (L34-37)
```go
	// recipient must not be empty
	if strings.TrimSpace(p.Recipient) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
	}
```

**File:** x/uexecutor/types/inbound.go (L165-171)
```go
	case TxType_FUNDS, TxType_GAS:
		if strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
		}
		if !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address: %s", p.Recipient)
		}
```

**File:** universalClient/chains/svm/tx_builder.go (L302-310)
```go
	var recipientPubkey solana.PublicKey
	recipientPubkey, err = solana.PublicKeyFromBase58(data.Recipient)
	if err != nil {
		hexBytes, hexErr := hex.DecodeString(removeHexPrefix(data.Recipient))
		if hexErr != nil || len(hexBytes) != 32 {
			return nil, fmt.Errorf("invalid recipient address format (expected Solana Pubkey): %s", data.Recipient)
		}
		recipientPubkey = solana.PublicKeyFromBytes(hexBytes)
	}
```

**File:** x/uexecutor/keeper/outbound.go (L99-119)
```go
// handleFailedOutbound mints back the bridged tokens to the revert recipient,
// then attempts to refund any excess gas (gasFee - gasFeeUsed) just like a
// successful outbound would. Both operations are recorded on the outbound.
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

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
