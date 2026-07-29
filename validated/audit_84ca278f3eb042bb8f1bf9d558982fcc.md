### Title
Unvalidated `GasToken` from user-emitted outbound events allows malicious-token drain of `UniversalCore`'s refund/swap flow - (File: `x/uexecutor/keeper/create_outbound.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`BuildOutboundsFromReceipt` decodes the `UniversalTxOutbound` event emitted by the `UniversalGatewayPC` contract and validates the bridged `Token` field against the `uregistry` whitelist, but it never applies the same validation to the sibling `GasToken` field. `GasToken` is fully attacker-controlled (set by whatever the attacker's own UEA-executed payload passes to the gateway contract) and later flows, unchecked, into `applyGasRefund` → `CallUniversalCoreRefundUnusedGas`, a module-originated `DerivedEVMCall` (`commit=true`) that has `UniversalCore` `approve()`/swap an attacker-supplied "PRC20" contract through Uniswap-style routing. This is the same bug class as the `OracleLess` finding: an unvalidated attacker-supplied token address is handed to privileged code that calls into it (`approve`) and relies on balance/swap accounting that the malicious contract can manipulate, allowing drainage of real PRC20/native funds pooled in `UniversalCore`.

### Finding Description
In `x/uexecutor/keeper/create_outbound.go`, the outbound builder validates `event.Token` (the bridged asset) against the registry: [1](#0-0) 

but copies `event.GasToken` straight from the decoded log with zero validation: [2](#0-1) 

`event.GasToken` itself is decoded directly from EVM log data with no origin check: [3](#0-2) 

This `GasToken` (and the attacker-controlled `GasFee`) is persisted onto `OutboundTx.GasToken`/`GasFee`. Once Universal Validators (honest, following protocol) observe the outbound as successful and report `gas_fee_used` less than the attacker-inflated `gas_fee`, `handleSuccessfulOutbound` computes an excess amount and issues a real, commit=true, module-sender `DerivedEVMCall` into `UniversalCore.refundUnusedGas`, passing the attacker's `gasToken` verbatim: [4](#0-3) 

`CallUniversalCoreRefundUnusedGas` performs a real, state-changing EVM call as the `uexecutor` module account: [5](#0-4) 

Inside `UniversalCore.refundUnusedGas(gasToken, amount, recipient, withSwap=true, ...)` the contract must call into the `gasToken` contract (approve/transferFrom) and route it through the swap logic — exactly the `tokenIn.safeApprove(target, amount)` pattern from the `OracleLess` report. Because `gasToken` is never checked against `uregistry.TokenConfigs`/`GetTokenConfigByPRC20` (the same check `Token` receives at line 60 of `create_outbound.go`), an attacker can point it at a contract they fully control, whose `approve`/`transferFrom` implementation runs arbitrary logic in the context of a call originated by the trusted `uexecutor` module account, targeting `UniversalCore` (a contract that pools real PRC20/native liquidity for the whole protocol).

### Impact Explanation
This breaks "corruption of ... gas fee accounting, refund accounting ... token mapping" and "unauthorized module-originated EVM execution" from the allowed-impact gate. The attack lets an unprivileged, ordinary user (anyone who can submit `MsgExecutePayload` and trigger a `UniversalGatewayPC` outbound event through their own UEA) get the protocol's own module account (`uexecutor`) to issue a privileged, funded EVM call into a contract the attacker chose, with the `withSwap=true` code path in `UniversalCore` invoking that malicious contract. Depending on `UniversalCore`'s internal accounting (mirroring `OracleLess`'s `execute()` balance-diff pattern), this can be leveraged to redirect or drain protocol-held PRC20/native (WPC) liquidity to the attacker, i.e. unauthorized release of protocol-controlled funds.

### Likelihood Explanation
Reachable by any unprivileged user through the ordinary `MsgExecutePayload` → UEA → gateway-contract path (no privileged role, no malicious validator/relayer assumption required — validators only honestly report `gas_fee_used` from the destination chain, which the attacker does not need to falsify to trigger the refund path, only needs `gas_fee` > `gas_fee_used`, which the attacker fully controls by setting `gas_fee` arbitrarily high in the payload that generates the outbound event). The only missing guard is a registry lookup that already exists one field over (`Token`) but was omitted for `GasToken`.

### Recommendation
Validate `event.GasToken` the same way `event.Token` is validated in `BuildOutboundsFromReceipt` — reject or refuse to persist/act on an outbound whose `GasToken` is not a known/whitelisted PRC20 in `uregistry.TokenConfigs` (via `GetTokenConfigByPRC20`), before it is ever passed into `CallUniversalCoreRefundUnusedGas`. Additionally, review `UniversalCore.refundUnusedGas`'s swap logic (out of this repo's scope but load-bearing) to ensure it does not trust balance deltas / approve-return-values from arbitrary ERC20 implementations, consistent with the recommendation in the source `OracleLess` report (whitelist token/target/txData).

### Proof of Concept
1. Attacker deploys a malicious ERC20-like contract `EvilGasToken` on Push Chain EVM whose `approve()`/`transferFrom()` performs an arbitrary reentrant call/transfer targeting `UniversalCore`'s real PRC20 or WPC balances.
2. Attacker calls their own UEA via `MsgExecutePayload`, whose payload calls `UniversalGatewayPC`'s bridge/outbound function with `gasToken = EvilGasToken`, `gasFee = <huge amount>`, and any legitimate whitelisted `token`/`amount` (so the `Token` check in `BuildOutboundsFromReceipt` passes).
3. The resulting `UniversalTxOutbound` event is decoded by `DecodeUniversalTxOutboundFromLog` and stored on `OutboundTx.GasToken`/`GasFee` unchecked (`create_outbound.go`).
4. Universal Validators observe the outbound on the destination chain and honestly report a small `gas_fee_used` via `MsgVoteOutbound`.
5. On quorum, `handleSuccessfulOutbound` → `applyGasRefund` computes `refundAmount = gasFee - gasFeeUsed` (attacker-inflated) and calls `CallUniversalCoreRefundUnusedGas(ctx, EvilGasToken, refundAmount, recipient, withSwap=true, ...)` as the trusted module account, `commit=true`.
6. `UniversalCore` interacts with `EvilGasToken`'s attacker-controlled code, which exploits the balance/approve-trust assumption to redirect real pooled PRC20/native funds to the attacker.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L59-67)
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L69-90)
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
```

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L76-96)
```go
	event.ChainId = values[i].(string)
	i++
	event.Target = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.Amount = values[i].(*big.Int)
	i++
	event.GasToken = values[i].(common.Address).Hex()
	i++
	event.GasFee = values[i].(*big.Int)
	i++
	event.GasLimit = values[i].(*big.Int)
	i++
	event.Payload = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.ProtocolFee = values[i].(*big.Int)
	i++
	event.RevertRecipient = values[i].(common.Address).Hex()
	i++
	event.TxType = SolidityTxTypeToProto(values[i].(uint8))
	i++
	event.GasPrice = values[i].(*big.Int)
```

**File:** x/uexecutor/keeper/outbound.go (L178-257)
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
	if err != nil {
		refundPcTx.Status = "FAILED"
		refundPcTx.ErrorMsg = err.Error()
	} else {
		refundPcTx.TxHash = resp.Hash
		refundPcTx.GasUsed = resp.GasUsed
		refundPcTx.Status = "SUCCESS"
	}

	outbound.PcRefundExecution = refundPcTx
	outbound.RefundSwapError = swapFallbackReason
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
