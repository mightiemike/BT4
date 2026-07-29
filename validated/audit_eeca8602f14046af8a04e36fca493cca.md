## Analysis

The Pod bug pattern is: a protocol-mandated action forces an unavoidable AMM/pool exit at a fee that is fixed by the protocol rather than bounded by the user's own preference (`maxFee`), letting an attacker who can influence the pool extract value at the user's expense.

Push Chain has the same shape in its gas-abstraction and gas-refund autoswap paths. Both `ExecuteInboundGas` (GAS inbound route) and `applyGasRefund` (outbound gas refund route) compute a swap quote from the on-chain Uniswap V3 `QuoterV2` and hard-code the slippage tolerance to 5%, with no per-user or per-tx override: [1](#0-0) [2](#0-1) 

The quote fetch (`GetSwapQuote`) and the swap execution (`CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`) happen back-to-back inside the same keeper call, itself invoked from the message handler that finalizes the inbound/outbound ballot (i.e., the last vote that reaches quorum) — there is no commit-reveal or TWAP protection between quoting and execution: [3](#0-2) 

### Title
Fixed 5% slippage on protocol-mandated gas-abstraction and gas-refund autoswaps enables sandwich-attack value extraction from users - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
When a user deposits a non-native gas token via the GAS/GAS_AND_PAYLOAD inbound route, or when validators finalize an outbound with excess unused gas, the uexecutor module forces a swap of the PRC20 gas token into native PC through the system's own Uniswap V3 pool. The minimum acceptable output (`minPCOut`) is always computed as `quote * 95 / 100` — a fixed 5% slippage band chosen by the protocol, not by the depositor/recipient. This is functionally the same defect as the reported Pod issue: the user cannot supply a `maxFee`/`maxSlippage` to cap the value lost during a swap that they did not choose to time.

### Finding Description
`ExecuteInboundGas` fetches a same-block quote via `GetSwapQuote` and immediately executes the swap with `minPCOut` derived from a hardcoded 5% tolerance: [4](#0-3) 

The identical pattern (fixed 5%, quote-then-swap in one call) is used for the excess-gas refund on outbound finalization in `applyGasRefund`: [5](#0-4) 

Both paths call the shared `GetSwapQuote`/`CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` helpers, which take `fee`/`minPCOut` purely as caller-supplied values with no protocol-level minimum-quality check other than the 5% figure baked into the caller: [6](#0-5) 

Because the on-chain Uniswap V3 pool used for this swap is a normal AMM pool that any unprivileged actor can trade against, and because the quote is fetched in the same transaction that triggers the swap (the `MsgVoteInbound`/`MsgVoteOutbound` that completes quorum), an unprivileged attacker who observes this transaction in the mempool can sandwich it: trade against the pool to move price unfavorably immediately before the quoted-then-executed swap lands, letting the depositor/refund-recipient absorb up to the full 5% band as slippage, and then reverse the trade afterward to capture the extracted value.

### Impact Explanation
This corrupts the amount of native PC actually credited to the depositor's UEA (gas-abstraction inbound path) or to the outbound refund recipient (gas-refund path) versus what an honest, unmanipulated quote would have produced — a direct loss of user/protocol-controlled value on every GAS/GAS_AND_PAYLOAD inbound and every outbound with excess gas, which are ordinary, frequently-triggered user flows. There is no user-facing parameter anywhere in the inbound/outbound message types to constrain the slippage tighter than 5%, so even a user who cares about minimizing MEV loss on small transfers has no way to opt for a stricter bound, unlike a normal DEX interaction where the trader supplies `amountOutMinimum` themselves.

### Likelihood Explanation
The swap is triggered automatically by ordinary, unprivileged actions (any external-chain gas-token deposit, or any outbound with `gasFeeUsed < gasFee`), requiring no privileged access. Sandwiching a same-block quote-then-swap sequence on a public-mempool EVM-compatible chain is a well-established, low-cost MEV technique, and the fixed 5% band is generous enough to make many sandwich attempts profitable relative to gas cost, especially on higher-value inbound/outbound gas amounts.

### Recommendation
Do not hard-code the slippage tolerance. Either (a) let the fee/slippage bound be derived from a validator-attested, time-weighted price rather than a single same-block spot quote, or (b) surface a user-supplied `minPCOut`/`maxSlippageBps` on the relevant inbound/outbound message so the affected party can bound their own worst-case loss, mirroring the `maxFee` recommendation from the original report. At minimum, separate the quote block from the execution block (e.g., quote at vote-cast time, execute at a later, unpredictable block) to remove the same-transaction sandwich window.

### Proof of Concept
1. Attacker monitors the mempool/observes validator voting progress for an inbound GAS deposit (or an outbound about to reach quorum with `gasFeeUsed < gasFee`).
2. Immediately before the finalizing `MsgVoteInbound`/`MsgVoteOutbound` lands, attacker submits a large swap against the same Uniswap V3 pool (`prc20 -> wpc` or reverse) used by `GetSwapQuote`/`CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`, pushing the pool price down by close to 5%.
3. The quorum-finalizing transaction executes `GetSwapQuote` against the now-manipulated pool, computes `minPCOut = quote * 95/100`, and the actual autoswap executes near the worst allowed price — crediting the depositor/refund recipient with up to ~5% less native PC than an honest quote would produce.
4. Attacker reverses their trade in a following transaction, capturing the value extracted from the victim's swap as arbitrage profit.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-153)
```go
					if execErr == nil {
						// --- step 4: fetch swap quote and compute minPCOut with 5% slippage
						var (
							quoterAddr common.Address
							wpcAddr    common.Address
							fee        *big.Int
							quote      *big.Int
						)

						quoterAddr, execErr = k.GetUniversalCoreQuoterAddress(sdkCtx)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}

						if execErr == nil {
							wpcAddr, execErr = k.GetUniversalCoreWPCAddress(sdkCtx)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							fee, execErr = k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

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
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/outbound.go (L213-237)
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
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}
```

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

**File:** x/uexecutor/keeper/evm.go (L540-593)
```go
// Calls Handler Contract to deposit prc20 tokens with auto-swap.
// fee and minPCOut must be pre-computed by the caller (see GetDefaultFeeTierForToken / GetSwapQuote).
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: depositPRC20WithAutoSwap",
		"prc20", prc20Address.Hex(),
		"recipient", to.Hex(),
		"amount", amount.String(),
		"fee", fee.String(),
		"min_pc_out", minPCOut.String(),
	)
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
}
```
