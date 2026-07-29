### Title
Outbound gas-refund path trusts un-validated `GasToken` from gateway event log for `refundUnusedGas` — ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`BuildOutboundsFromReceipt` decodes the `UniversalTxOutbound` event emitted by `UNIVERSAL_GATEWAY_PC` and validates only the bridged asset (`event.Token`) against the registry via `GetTokenConfigByPRC20`. The event's `GasToken` field is copied straight onto `OutboundTx.GasToken` with no equivalent registry lookup/validation. That unvalidated address is later fed directly into a module-originated EVM call (`CallUniversalCoreRefundUnusedGas`) that swaps/mints funds to a recipient — the same bug class as the Solana report: a parameter that determines the "mint"/token semantics of a value-moving CPI/call is taken from caller-influenced data without validating it is the expected, registered asset with the expected decimal scale.

### Finding Description
In `BuildOutboundsFromReceipt` [1](#0-0) , the bridged token address `event.Token` is checked against `uregistryKeeper.GetTokenConfigByPRC20` before being trusted, but `event.GasToken` is assigned to `outbound.GasToken` with **no corresponding lookup or validation** against the token registry.

That `GasToken` value later drives real fund movement in `applyGasRefund`, which parses it straight into an EVM address and passes it to `CallUniversalCoreRefundUnusedGas` as the token to swap/deposit: [2](#0-1) . `CallUniversalCoreRefundUnusedGas` is a module-as-sender `DerivedEVMCall` that mints/deposits/swaps based on the caller-supplied `gasToken` address [3](#0-2) .

This mirrors the Solana report precisely: `depositPRC20`/`CallPRC20Deposit`'s deposit path is safe because the PRC20 address is derived from a registry-validated `TokenConfig.NativeRepresentation.ContractAddress` (see `handler.go`) [4](#0-3) , and `event.Token`/`ExternalAssetAddr` go through the same registry check in `create_outbound.go`. But `GasToken` skips this check entirely — it is whatever the EVM payload execution (triggered by the user's own `MsgExecutePayload`, which is permissionless and gasless) emitted in the gateway event, with the "identify the specific mint/asset" validation (the exact fix class in the Solana report: "include parameters that identify the specific mint... assert decimals") never applied on the Push Chain side.

### Impact Explanation
If `GasToken` is not independently enforced to be a registered token address on the Solidity contract side (which I could not verify — `UNIVERSAL_GATEWAY_PC`/`UniversalCore.sol` live in the separate `push-chain-core-contracts` repo, outside this repo's scope and not indexed here), an unprivileged user could craft a universal payload that triggers an outbound event with an attacker-chosen `GasToken` address (e.g., a token with mismatched decimals, or a token PRC20 that is not registered/mapped to any external chain asset). `applyGasRefund` would then call `refundUnusedGas`/swap logic against this attacker-chosen token, potentially causing gas-refund accounting corruption, minting/swapping the wrong asset, or misrouting value to the "recipient" for a token that was never validated to be gas-equivalent — corrupting gas fee accounting/refund accounting, which is explicitly in scope.

### Likelihood Explanation
Medium-to-Low confidence/likelihood given information available: the attacker-reachability chain (user submits `MsgExecutePayload` → UEA executes arbitrary EVM logic → gateway emits `UniversalTxOutbound` with attacker-influenced `GasToken`) is plausible and gasless/permissionless, matching "ordinary user deposits/payloads" scope. However, I was unable to confirm from the indexed Go codebase whether the Solidity gateway/UniversalCore contracts already constrain `GasToken` to a registry-approved set on-chain before emitting the event (this validation, if it exists, would live entirely in the external contracts repo, which is out of this index's coverage). This is a genuine unknown that should be verified against `push-chain-core-contracts` before treating this as a confirmed, exploitable vulnerability.

### Recommendation
Before consuming `event.GasToken` in `BuildOutboundsFromReceipt`, validate it against `uregistryKeeper` (e.g. `GetTokenConfigByPRC20` or an equivalent gas-token allowlist) exactly as is already done for `event.Token`, and reject/flag outbounds whose `GasToken` is not a recognized, correctly-scaled registered asset. Additionally confirm (or add) a matching enforcement on the Solidity `UniversalCore`/gateway contract side so `GasToken` can only ever be a pre-approved gas asset, closing the gap symmetrically with how the bridged-asset address is already validated.

### Proof of Concept
Not independently executable from this repository alone — reproducing it requires the `push-chain-core-contracts` Solidity source (`UniversalGatewayPC`, `UniversalCore`) to construct a payload that causes the gateway to emit `UniversalTxOutboundEventSig` with an attacker-chosen `GasToken`, which is outside this repo's indexed scope. On the Go side, the reachable gap is demonstrated by comparing the validation asymmetry between `event.Token` (checked via `GetTokenConfigByPRC20`, `create_outbound.go:60-67`) and `event.GasToken` (assigned unchecked, `create_outbound.go:80`), then traced to its consumption in `applyGasRefund` → `CallUniversalCoreRefundUnusedGas` (`outbound.go:198-230`, `evm.go:595-644`).

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L59-90)
```go
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
```

**File:** x/uexecutor/keeper/outbound.go (L198-230)
```go
	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
```

**File:** x/uexecutor/keeper/evm.go (L595-644)
```go
// CallUniversalCoreRefundUnusedGas calls refundUnusedGas on UniversalCore to return excess gas fee
// to the recipient. withSwap=true swaps the gas token back to PC; withSwap=false deposits PRC20 directly.
func (k Keeper) CallUniversalCoreRefundUnusedGas(
	ctx sdk.Context,
	gasToken common.Address,
	amount *big.Int,
	recipient common.Address,
	withSwap bool,
	fee *big.Int,
	minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	// fee is uint24 in Solidity — pass as *big.Int (go-ethereum ABI packs non-standard widths as *big.Int)
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress,
		handlerAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"refundUnusedGas",
		gasToken,
		amount,
		recipient,
		withSwap,
		fee,
		minPCOut,
	)
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
