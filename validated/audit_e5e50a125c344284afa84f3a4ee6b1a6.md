## Finding [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unvalidated `GasToken` PRC20 address lets outbound gas refunds mint an arbitrary token - (File: x/uexecutor/keeper/outbound.go)

### Summary
`applyGasRefund` accepts `outbound.GasToken` — a value copied verbatim from the `UniversalTxOutbound` EVM log's `gasToken` field — and passes it straight into `CallUniversalCoreRefundUnusedGas`, which mints/swaps that PRC20 to the refund recipient. Unlike the bridged asset field (`event.Token`), which `BuildOutboundsFromReceipt` validates through `uregistryKeeper.GetTokenConfigByPRC20` before it is trusted, `event.GasToken` is never checked against the token registry or against the chain's canonical gas token mapping (`gasTokenPRC20ByChainNamespace`, visible in the UniversalCore ABI) anywhere in the Go keeper path.

### Finding Description
`BuildOutboundsFromReceipt` decodes the `UniversalTxOutbound` event and populates `OutboundTx.GasToken = event.GasToken` directly, with no registry lookup: [4](#0-3) 

Compare this to the same function's handling of the bridged asset token, which is resolved and validated via `GetTokenConfigByPRC20` before being trusted: [5](#0-4) 

Later, when an outbound is observed (success or failure), `applyGasRefund` uses this unchecked `outbound.GasToken` value to invoke `UniversalCore.refundUnusedGas`, which mints (or swaps-and-mints) PRC20 tokens to the refund recipient: [6](#0-5) [7](#0-6) 

Notably, `CallUniversalCoreRefundUnusedGas` never passes the outbound's destination chain/namespace, so the contract call itself has no cross-reference point to verify that `gasToken` is the legitimate gas-paying PRC20 for that chain — the entire trust boundary rests on the Go-side value copied from the event.

This is structurally identical to the reported Vester/VesterNLP pattern: a generic value-moving function (`withdrawToken` there, `applyGasRefund`/`refundUnusedGas` here) accepts *any* token address without excluding/validating a specific privileged one (`esToken` there, the canonical per-chain gas-token PRC20 here), while a parallel code path in the same module *does* perform that validation for a similar field (`GetTokenConfigByPRC20` on `event.Token`).

### Impact Explanation
If the `gasToken` value embedded in the `UniversalTxOutbound` event can be set by the transaction/payload that triggers it (a normal, unprivileged user action creating an outbound), an attacker can select an arbitrary registered PRC20 (e.g., a real, valuable USDC PRC20) as their outbound's "gas token." Because `gasFee` and `gasFeeUsed` are attacker/validator-observation-driven numeric fields with no accounting tie to funds actually escrowed for that PRC20, `applyGasRefund` will mint (via `CallPRC20DepositAutoSwap`/`refundUnusedGas`) that PRC20 to the refund recipient without any real backing having been locked — an unauthorized mint of protocol-controlled tokens, corrupting PRC20 supply/accounting invariants.

### Likelihood Explanation
The likelihood of full exploitability cannot be confirmed within this repository's scope: the Solidity source for `UniversalGatewayPC`/`UniversalCore` (which determines whether the `gasToken` parameter emitted in the event is genuinely attacker-settable, or independently derived/validated on-chain from `gasTokenPRC20ByChainNamespace`) is not present in this repository. The Go keeper code itself, however, demonstrably performs no independent validation of `GasToken` where an analogous check exists for `Token`/`Prc20AssetAddr`, which is the exact asymmetry the external report flags as the root cause class.

### Recommendation
In `BuildOutboundsFromReceipt` (`x/uexecutor/keeper/create_outbound.go`), validate `event.GasToken` the same way `event.Token` is validated — e.g., cross-check it against the registry's expected gas-token PRC20 for `event.ChainId` (mirroring the contract's own `gasTokenPRC20ByChainNamespace` mapping) before storing it on `OutboundTx.GasToken`. Reject or clamp outbounds whose `GasToken` does not match the registered value for the destination chain, and add an explicit require/guard in `applyGasRefund` (`x/uexecutor/keeper/outbound.go`) before calling `CallUniversalCoreRefundUnusedGas`.

### Proof of Concept
1. Attacker submits a transaction that causes `UniversalGatewayPC` to emit a `UniversalTxOutbound` event with `gasToken` set to a high-value registered PRC20 address (e.g., PRC20-USDC) instead of the legitimate/expected gas token for the destination chain.
2. `BuildOutboundsFromReceipt` copies this into `OutboundTx.GasToken` with no validation. [8](#0-7) 
3. Validators honestly observe and vote the outbound as successful with `gasFeeUsed < gasFee` (or the whole `gasFee` as excess in the failed-execution path).
4. `applyGasRefund` computes `refundAmount = gasFee - gasFeeUsed` and calls `CallUniversalCoreRefundUnusedGas(ctx, gasToken=<attacker-chosen PRC20>, refundAmount, recipient, ...)`, minting/depositing that PRC20 to the attacker-controlled recipient with no corresponding value ever locked for that specific PRC20. [9](#0-8)

### Citations

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L14-29)
```go
type UniversalTxOutboundEvent struct {
	TxID            string   // 0x... bytes32
	Sender          string   // 0x... address
	ChainId         string   // destination chain (CAIP-2 string)
	Token           string   // 0x... ERC20 or zero address for native
	Target          string   // 0x-hex encoded bytes (non-EVM recipient)
	Amount          *big.Int // amount of Token to bridge
	GasToken        string   // 0x... token used to pay gas fee
	GasFee          *big.Int // amount of GasToken paid to relayer
	GasLimit        *big.Int // gas limit for destination execution
	Payload         string   // 0x-hex calldata
	ProtocolFee     *big.Int // fee kept by protocol
	RevertRecipient string   // where funds go on full revert
	TxType          TxType   // ← single source of truth from proto
	GasPrice        *big.Int // gas price on destination chain at time of outbound
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L59-91)
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
		}
```

**File:** x/uexecutor/keeper/outbound.go (L178-245)
```go
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

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
			swapFallbackReason = fmt.Sprintf("swap refund failed: %s", err.Error())
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
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
