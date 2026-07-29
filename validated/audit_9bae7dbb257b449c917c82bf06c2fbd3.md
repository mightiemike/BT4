## Analysis

The reported bug class is "protocol swaps hard-code zero minimum-out, so swaps are vulnerable to sandwich/front-running." Push Chain's `x/uexecutor` module already partially "fixes" this class by computing a 5% slippage bound — but the *reference price itself* is fetched from the very same manipulable Uniswap-V3-style pool immediately before the swap executes, so the "protection" only bounds slippage relative to whatever price an attacker has just set, not relative to a trustworthy external price. This reproduces the exact root cause the external report calls out for `_sellTokenFor()` (spot-price-derived minimums, no oracle/TWAP).

### Title
Slippage protection for gas-abstraction swaps is anchored to a manipulable spot quote, enabling sandwich attacks on `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap` / `applyGasRefund` - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
Every PRC20→WPC gas-abstraction swap performed by the `uexecutor` module (inbound gas swap, gas+payload deposit swap, and gas-fee-refund swap) computes its `minPCOut` slippage floor from a quote fetched via `QuoterV2.quoteExactInputSingle` on the same pool, at essentially the same block, immediately before executing the swap via `depositPRC20WithAutoSwap`/`refundUnusedGas`. Because the "protected" price is derived from the pool's current instantaneous state rather than a TWAP or external oracle, an attacker can move the pool price with a front-run trade before the validator's finalizing vote transaction lands, causing the on-chain quote (and therefore the computed 5% floor) to reflect the manipulated price. The swap then "passes" its own slippage check while executing at an attacker-favorable price, and the attacker back-runs to capture the difference — a classic sandwich attack that the added slippage logic does not prevent.

### Finding Description
`GetSwapQuote` at [1](#0-0)  calls `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96 = 0`, i.e., a spot quote against the pool's current reserves, not a time-weighted average. This quote is used directly to derive `minPCOut` with a flat 5% haircut in three call sites:

- `ExecuteInboundGas`: [2](#0-1) 
- `gasAndPayloadDepositAutoSwap`: [3](#0-2) 
- `applyGasRefund`: [4](#0-3) 

All three then call `CallPRC20DepositAutoSwap` or `CallUniversalCoreRefundUnusedGas`, which perform the actual on-chain swap via `DerivedEVMCall` using the just-computed `minPCOut`: [5](#0-4) .

The whole quote→execute sequence is triggered synchronously inside `VoteInbound`, specifically by the validator whose vote finalizes the inbound ballot: [6](#0-5) . This means the exact block (and often the exact transaction) in which the swap will execute is knowable in advance from the public mempool of `MsgVoteInbound` transactions, once enough validators have voted to be one vote away from quorum.

An unprivileged attacker who is *not* a validator can:
1. Watch the `x/uvalidator` inbound ballot and see it is one vote from quorum.
2. Submit a large trade against the PRC20/WPC Uniswap pool immediately before the finalizing `MsgVoteInbound` transaction is included (front-run within the same block, or in the block immediately prior).
3. Let the module's swap execute against the now-skewed pool — `GetSwapQuote` reads the skewed price, `minPCOut` is computed from that skewed price, so the "protected" swap still executes at the bad rate and does not revert.
4. Back-run to unwind the manipulation and capture the difference, at the expense of the value that should have flowed to the UEA/recipient (or, for refunds, to the outbound sender).

This is not prevented by "existing guards" — the 5% band only bounds *additional* slippage between the moment `GetSwapQuote` is read and the moment `depositPRC20WithAutoSwap`/`refundUnusedGas` executes; it does nothing to bound deviation from a fair/external price, which is exactly the gap the original report calls out.

### Impact Explanation
Attacker-controlled pool manipulation causes the module (acting on behalf of ordinary bridge users) to execute PRC20→PC swaps at attacker-favorable prices during: (a) gas-abstraction inbound processing (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`), which determines how much native PC funds a user's UEA receives for gas, and (b) gas-fee refunds (`applyGasRefund`), which determines how much excess gas is returned to the outbound sender. In both cases this is value taken from protocol/user-controlled funds during a state transition reachable by an ordinary unprivileged user's cross-chain deposit — falling within "corruption of gas fee accounting, refund accounting" and unauthorized loss of user-controlled funds.

### Likelihood Explanation
Exploitation requires only: (1) monitoring public `MsgVoteInbound` votes/ballot status (public consensus data, no privileged access), and (2) enough capital/flash-loan liquidity to move the specific PRC20/WPC pool briefly. No validator, TSS, or governance compromise is needed — the trigger is available to any ordinary chain user submitting normal swap transactions against a public AMM pool that the module also uses as its price reference.

### Recommendation
Do not derive `minPCOut` from a spot quote fetched in the same block/transaction as execution. Instead: use a TWAP-based quote (e.g., Uniswap V3 `observe`/oracle cardinality-backed average) with a bounded lookback window, and/or apply a maximum-deviation check between the spot price and the TWAP before allowing the swap to proceed (reverting/falling back to a no-swap direct deposit if deviation exceeds a safe threshold, similar to the existing no-swap fallback path already present in `applyGasRefund`).

### Proof of Concept
1. Attacker observes an inbound event with `TxType_GAS` nearing ballot quorum (visible via `x/uvalidator` ballot state / public validator votes).
2. Attacker submits a large swap into the relevant PRC20/WPC pool to skew the pool price, timed to land in the same or immediately preceding block as the quorum-finalizing `MsgVoteInbound`.
3. When the finalizing vote executes, `VoteInbound` → `ExecuteInbound` → `ExecuteInboundGas` calls `GetSwapQuote` [7](#0-6)  against the now-skewed pool, computes `minPCOut` from that skewed quote [8](#0-7) , and executes `depositPRC20WithAutoSwap`, which succeeds because the check only compares against the already-corrupted reference price.
4. Attacker back-runs with an opposite trade to restore the pool and realize profit equal to the value extracted from the bridge user's swap, while the user's UEA receives less native PC than a fair-price swap would have produced.

### Citations

**File:** x/uexecutor/keeper/evm.go (L500-538)
```go
// GetSwapQuote calls QuoterV2.quoteExactInputSingle (commit=false) to get the expected
// output amount for swapping prc20 → wpc.
func (k Keeper) GetSwapQuote(
	ctx sdk.Context,
	quoterAddr, prc20Address, wpcAddress common.Address,
	fee, amount *big.Int,
) (*big.Int, error) {
	quoterABI, err := types.ParseUniswapQuoterV2ABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse QuoterV2 ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	params := types.AbiQuoteExactInputSingleParams{
		TokenIn:           prc20Address,
		TokenOut:          wpcAddress,
		AmountIn:          amount,
		Fee:               fee,
		SqrtPriceLimitX96: big.NewInt(0),
	}

	receipt, err := k.evmKeeper.CallEVM(ctx, quoterABI, ueModuleAccAddress, quoterAddr, false, nil, "quoteExactInputSingle", params)
	if err != nil {
		return nil, errors.Wrap(err, "QuoterV2 quoteExactInputSingle failed")
	}

	results, err := quoterABI.Methods["quoteExactInputSingle"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack quoteExactInputSingle result")
	}

	amountOut, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for amountOut: %T", results[0])
	}

	return amountOut, nil
}
```

**File:** x/uexecutor/keeper/evm.go (L574-592)
```go
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // who is sending the transaction
		handlerAddr,        // destination: Handler contract
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-148)
```go
						if execErr == nil {
							quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L213-234)
```go
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
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L148-155)
```go
	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
	}
```
