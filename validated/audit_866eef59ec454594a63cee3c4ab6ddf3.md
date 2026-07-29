### Title
Unbounded slippage-protected AutoSwap sourced from live, manipulable Uniswap V3 spot price - ([File: x/uexecutor/keeper/evm.go])

### Summary
### Finding Description
The external report's core defect is a "conversion rate" that is (1) derived from a live, mutable quantity and (2) computed and consumed in a way that lets an unprivileged actor bias the value used against another party before it is locked in. In Push Chain's `uexecutor` module, the deposit-autoswap path for `TxType_GAS` and `TxType_GAS_AND_PAYLOAD` inbounds reproduces the same structural pattern using a Uniswap V3 pool instead of a shares pool.

`ExecuteInboundGas` fetches a swap quote directly from the on-chain `QuoterV2.quoteExactInputSingle` (which reflects the pool's current spot/sqrt price) and immediately derives slippage protection from that same quote: [1](#0-0) 

The quote call itself: [2](#0-1) 

`minPCOut` is then computed as `quote * 95 / 100` — a fixed 5% slippage band around whatever the pool's price happens to be at execution time — and passed straight into `depositPRC20WithAutoSwap`: [3](#0-2) 

The same quote→95%→autoswap pattern is duplicated for `GAS_AND_PAYLOAD` inbounds and for outbound gas refunds: [4](#0-3) [5](#0-4) 

Because the quote source is a live AMM pool that lives on the same EVM state that ordinary users can freely trade against (any account can call the Uniswap V3 router/pool contracts on Push Chain), an unprivileged attacker can push the PRC20/WPC pool price to an extreme in a swap transaction, then let (or force, via timing of their own bridge deposit) the module's `GetSwapQuote` → `minPCOut` computation read the skewed price in the same or an adjacent block, and revert the pool price back afterward. Because `minPCOut` is derived from the manipulated quote rather than from an independent, attacker-resistant reference price (e.g., a TWAP or chain-meta-reported price), the "slippage protection" protects against nothing — it is computed off the very number the attacker just distorted, exactly as in the reported bug where `self.shares_bonded` (mutable by an unprivileged unbond call) fed the very ratio it was used to compute.

### Impact Explanation
This lets an attacker extract value from every user (or from module-controlled liquidity) whose inbound gas deposit is autoswapped during the window the attacker controls the pool price:
- Depositing user's PRC20 gets swapped into PC at an attacker-manipulated rate, so the recipient UEA receives materially less PC than the honest market rate implies — a direct value loss to the depositing user, matching the "unauthorized loss of user funds" and "PRC20/native asset accounting corruption" impact categories in scope.
- Because the 5% band is computed off the corrupted spot price rather than an independent reference, the deviation is bounded only by how far the attacker can move the pool in one manipulation, not by the true market price, so losses beyond the intended 5% tolerance are possible.
- The same pattern in `applyGasRefund` / `gasAndPayloadDepositAutoSwap` similarly lets an attacker degrade the value that any user's gas refund gets converted to.

### Likelihood Explanation
Requires: (1) a Uniswap V3 pool for the PRC20/WPC pair with exploitable liquidity/depth, and (2) the ability to trade against that pool and control transaction ordering around the module's autoswap call within the same block or a short window. Both preconditions are plausible for an unprivileged EOA on an EVM-compatible chain (no privileged role needed), though the achievable profit is bounded by the pool's liquidity and by the fact that Push Chain, as a single-block-producer-per-height chain, limits (but does not eliminate) an attacker's ability to guarantee same-block sandwiching around validator-triggered `VoteInbound`/`ExecuteInboundGas` execution — full confirmation of the exact call-timing (whether `ExecuteInboundGas` runs synchronously within the finalizing validator's `MsgVoteInbound` transaction or in a later block) could not be completed in the available time, which affects how tight the attacker's control over ordering can be. This is a design-level economic/oracle issue rather than a missing single check, similar to the original report's characterization ("improperly aligned economic incentives").

### Recommendation
Do not derive `minPCOut` solely from the same live `QuoterV2` call executed immediately before the swap. Instead:
- Use a manipulation-resistant reference price (e.g., a time-weighted average price, or the already-existing `ChainMeta`/gas-price oracle infrastructure that validators vote on) to bound acceptable swap output, independent of the instantaneous pool state.
- Alternatively, widen and dynamically adjust the slippage tolerance based on observed pool depth, or cap the deposit amount that can be auto-swapped in a single block relative to pool liquidity.
- Consider requiring the quote and the swap to be validated against a price computed over multiple blocks/observations rather than a single spot read.

### Proof of Concept
1. Attacker identifies the Uniswap V3 pool backing the PRC20 token used for an upcoming inbound gas deposit (`GetDefaultFeeTierForToken` / `GetUniversalCoreQuoterAddress` reveal pool/fee/quoter addresses which are public state).
2. Attacker submits a large swap against that pool to move its price sharply against the PRC20 side.
3. When the queued inbound `GAS` (or `GAS_AND_PAYLOAD`) UniversalTx is executed via `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`, `GetSwapQuote` reads the now-skewed spot price, and `minPCOut = quote*95/100` inherits that skew.
4. `CallPRC20DepositAutoSwap`/`gasAndPayloadDepositAutoSwap` executes the swap against the manipulated pool, converting the victim's PRC20 into PC at the bad rate.
5. Attacker reverses their initial swap to restore the pool price, pocketing the difference extracted from the victim's deposit/refund.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-146)
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

**File:** x/uexecutor/keeper/evm.go (L540-592)
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
