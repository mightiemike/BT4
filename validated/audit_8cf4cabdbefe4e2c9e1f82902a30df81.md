## Finding: Same-block spot-price self-reference defeats autoswap slippage protection during PRC20→PC deposit conversion

The external report's core bug class — using a manipulable, current-block balance/price as both the "market price" and the basis for a "safety" check — has a native analog in Push Chain's gas-token autoswap flow.

### Title
Self-Referential Spot-Price Slippage Bound Allows Same-Block AMM Manipulation During GAS/GAS_AND_PAYLOAD Autoswap Deposits - ([File: x/uexecutor/keeper/evm.go], [File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go], [File: x/uexecutor/keeper/outbound.go])

### Summary
For GAS and GAS_AND_PAYLOAD inbound deposits (and for outbound unused-gas refunds), the uexecutor keeper computes the minimum acceptable output of a PRC20→WPC swap (`minPCOut`) by taking a live spot-price quote from the Uniswap V3 `QuoterV2` contract and applying a fixed 5% haircut, then immediately passes that same self-derived bound into the actual swap call. Because both the "reference price" and the "slippage floor" come from the same instantaneous, unprotected AMM read, an attacker who moves the pool's spot price in the same block (a classic no-TWAP AMM manipulation, structurally identical to the LP.sol flashloan bug) can force the protocol-triggered autoswap to execute at a manipulated price while still satisfying its own manipulated `minPCOut` check.

### Finding Description
`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `commit=false`, i.e. a plain read of the pool's current state (no TWAP, no historical observation window): [1](#0-0) 

This quote is used directly to compute `minPCOut = quote * 95 / 100` and passed straight into the state-changing swap call `CallPRC20DepositAutoSwap` in the same logical execution path, with no independent price source or dislocation check: [2](#0-1) [3](#0-2) 

The identical pattern is reused for the outbound unused-gas refund swap (`gasToken → PC`): [4](#0-3) 

`CallPRC20DepositAutoSwap` then invokes `depositPRC20WithAutoSwap` on the `UniversalCore` system contract as a module-originated `DerivedEVMCall`, carrying the attacker-influenceable `fee`/`minPCOut` values into the actual on-chain swap: [5](#0-4) 

Nothing in this Go-level flow enforces a TWAP, a max-deviation-from-recent-price check, or a minimum pool liquidity threshold before trusting the quote — exactly the missing protections the external report calls out (avoid current/manipulable variables, require TWAP, require minimum liquidity, cross-check multiple price sources).

### Impact Explanation
The `UniversalCore` swap pool underpins conversion of every inbound GAS / GAS_AND_PAYLOAD deposit's PRC20 gas token into native PC for the recipient's UEA, and also underpins outbound gas-fee refunds. If an attacker manipulates the relevant Uniswap V3 pool's spot price in the same block that a queued inbound/outbound autoswap executes (timing a large swap against the pool right before the validator-driven `ExecuteInboundGas`/`applyGasRefund` derived transaction lands), the `minPCOut` floor is computed off the same skewed price and offers no real protection. The autoswap then converts the user's/protocol's PRC20 at an unfavorable rate, and the attacker can unwind their price-moving position afterward, extracting the difference from the pool at the expense of the depositing user (or the protocol, in the refund path). This corrupts PC/PRC20 accounting for the deposit and refund legs and results in fund loss for ordinary, unprivileged users performing normal cross-chain deposits — squarely in scope under "corruption of PRC20 or native asset accounting" and "unauthorized module-originated EVM execution" causing "unauthorized ... loss ... of user or protocol-controlled funds."

### Likelihood Explanation
This requires no privileged access — any unprivileged actor with capital can submit ordinary EVM swap transactions against the pool on Push Chain to move its spot price, then trigger or wait for their own pending cross-chain deposit (which they control the timing of, since they choose when to submit the source-chain event that becomes the inbound) to be processed shortly after. Liquidity thresholds are not checked in the Go code before trusting the quote, so the attack is more practical on newer, thinner PRC20/WPC pools. This mirrors the exact "trivial to manipulate via flash loan" scenario described in the external report, adapted to Push Chain's own AMM-integration code rather than Balancer's.

### Recommendation
- Do not derive `minPCOut` solely from the same-block `QuoterV2.quoteExactInputSingle` spot read. Use a TWAP-based quote (e.g. Uniswap V3 `observe`-based TWAP) or an independent oracle price, and only fall back to spot pricing when the pool has demonstrated sufficient liquidity/duration.
- Enforce a maximum allowed deviation between the spot quote and a longer-window reference price before accepting `minPCOut`; abort/revert (fall back to non-swap deposit path, which already exists) if the deviation exceeds a safe bound.
- Consider gating autoswap on a minimum on-chain liquidity threshold for the specific `prc20/WPC` pool, consistent with the "Sense" recommendation in the source report.

### Proof of Concept
1. Attacker identifies a `prc20 ↔ WPC` Uniswap V3 pool used by `UniversalCore` with thin liquidity.
2. Attacker initiates a legitimate cross-chain GAS deposit inbound (e.g. small amount) that will be routed through `ExecuteInboundGas` once validators reach quorum.
3. Shortly before (or in the same block as) validator quorum finalization, attacker submits a large swap on Push Chain EVM against the `prc20/WPC` pool to skew the spot price downward for `prc20`.
4. When `ExecuteInboundGas` runs, `GetSwapQuote` reads the skewed spot price; `minPCOut` is computed as 95% of that already-bad quote.
5. `CallPRC20DepositAutoSwap` executes `depositPRC20WithAutoSwap`, which succeeds because `minPCOut` was derived from the same manipulated price rather than a fair one — the deposit converts at a bad rate.
6. Attacker reverses their large swap, restoring the pool price and recouping their capital plus the value extracted from the mispriced autoswap, at the expense of the depositor's converted PC balance.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-379)
```go
// gasAndPayloadDepositAutoSwap handles the swap quote + deposit autoswap for GAS_AND_PAYLOAD.
func (k Keeper) gasAndPayloadDepositAutoSwap(
	sdkCtx sdk.Context,
	prc20AddressHex common.Address,
	ueaAddr common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	wpcAddr, err := k.GetUniversalCoreWPCAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	fee, err := k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
	if err != nil {
		return nil, err
	}

	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
}
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
