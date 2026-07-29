This confirms the critical detail: `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` is executed synchronously inside the `MsgVoteInbound` transaction handler that delivers the finalizing (quorum-reaching) vote [1](#0-0) . This means the block/transaction in which the swap executes is deterministically the block containing the last quorum vote — an attacker watching pending votes (public, since `MsgVoteInbound` txs are ordinary mempool transactions) can predict exactly which block will trigger the swap and submit a manipulation trade in that same block.

### Title
Gas-abstraction auto-swap relies on a manipulable instantaneous QuoterV2 price, enabling sandwich extraction from bridge deposits - (x/uexecutor/keeper/execute_inbound_gas.go, execute_inbound_gas_and_payload.go, evm.go)

### Summary
`GetSwapQuote` reads the Uniswap-V3-style `QuoterV2.quoteExactInputSingle` spot price at call time [2](#0-1) , and this value is used, unmodified except for a flat 5% haircut, as the slippage floor `minPCOut` passed into `CallPRC20DepositAutoSwap` / `depositPRC20WithAutoSwap` [3](#0-2) [4](#0-3) . The identical pattern is used for gas refunds in `applyGasRefund`/`getSwapQuoteForRefund` [5](#0-4) .

### Finding Description
`minPCOut = quote * 95 / 100` only bounds price impact *within* the swap being executed relative to the pool state at the moment `GetSwapQuote` is read — it provides no protection against the pool's spot price having been externally skewed just before the quote is taken. Because `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` run inline inside the `MsgVoteInbound` handler for the finalizing vote [1](#0-0) , and pending inbound votes/ballots are publicly observable on-chain state before quorum is reached (an unprivileged party can see when a ballot is one vote away from quorum), an attacker can:
1. Predict the exact block in which the deposit's auto-swap will execute.
2. Submit an ordinary EVM transaction in that block that pushes the PRC20/WPC pool's spot price against the depositor (e.g., dumping PRC20 into the pool to depress its WPC value) before the module's `GetSwapQuote`/`CallPRC20DepositAutoSwap` calls run.
3. Since `GetSwapQuote` picks up this already-degraded price and `minPCOut` is only 95% of that same degraded quote, the module-originated swap executes at the manipulated (bad) price, crediting the user's UEA with less PC than a fair, unmanipulated market price would produce.
4. Reverse the manipulation trade afterward (in the same or a following block) to restore the pool and pocket the value that was pulled from the depositor's swap.

This is a standard "spot-price-as-oracle" AMM manipulation pattern: using the AMM's own instantaneous price as the reference for slippage protection, instead of a manipulation-resistant reference (TWAP, external oracle, or a price bound fixed at deposit-observation time on the source chain), makes the "fair price" guarantee illusory. The entry point (submitting ordinary swap transactions against the pool contract) requires no privileged role — any unprivileged Push Chain user can trade against the pool.

### Impact Explanation
The depositing user's UEA is credited with a PC amount computed from a price the attacker controls, extracting value that should have accrued to the depositor, corrupting deposit/refund accounting for gas-abstraction inbound flows (`GAS`, `GAS_AND_PAYLOAD`, and the outbound gas-refund path). This matches the in-scope impact category "corruption of ... gas fee accounting, refund accounting" and value extraction from user funds.

### Likelihood Explanation
Exploitability depends on: (a) the attacker being able to predict/target the exact finalizing-vote block (feasible since ballots and vote counts are on-chain and quorum thresholds are computable), (b) the attacker having enough capital/liquidity-relative size to move the pool price meaningfully within the 5% slippage band's effective bound, and (c) the attacker's manipulation transaction landing in the same block ahead of the finalizing vote's processing (subject to normal block-proposer transaction ordering, not guaranteed but achievable via gas-price bidding or proposer cooperation in many chains). The magnitude of extractable value is capped by real pool depth and the 5% band, so impact scales with pool liquidity and deposit size — likely moderate rather than catastrophic unless the PRC20/WPC pool is thin.

### Recommendation
Do not derive `minPCOut` solely from an instantaneous `quoteExactInputSingle` call taken in the same execution as the swap. Use a manipulation-resistant reference price (e.g., a time-weighted average price over multiple blocks, a governance/oracle-set reference price, or a price bound derived at the time the inbound was originally observed/finalized on the source chain rather than at swap-execution time), and/or cap the maximum per-block price deviation allowed for the pool used by `UNIVERSAL_CORE`'s auto-swap, and/or reduce reliance on this specific pool by widening liquidity requirements or adding a circuit breaker if the spot price deviates sharply from a longer-window average.

### Proof of Concept
1. Attacker monitors `MsgVoteInbound` submissions for a pending `GAS`/`GAS_AND_PAYLOAD` inbound ballot and determines it needs exactly one more vote to reach quorum (`votesNeeded` in `VoteOnInboundBallot`) [6](#0-5) .
2. In the same block the final quorum vote is expected to land, attacker submits a large PRC20→WPC (or WPC→PRC20) trade against the pool at `quoterAddr`/pool used by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`, driving the spot price against the pending depositor.
3. The finalizing `MsgVoteInbound` executes, triggering `ExecuteInboundGas` → `GetSwapQuote` (reads the now-skewed price) → `CallPRC20DepositAutoSwap` with `minPCOut` computed from that skewed quote [7](#0-6) .
4. The depositor's UEA is credited with PC computed at the manipulated rate.
5. Attacker reverses the trade to restore the pool price and realizes the extracted value, net of the 5% band and their own trading costs.

Note: I could not fully verify within the index whether the pool/quoter contract used here is exclusively an internal Push Chain system contract with restricted liquidity provisioning (which would bound attacker capital efficiency) or a more generally accessible AMM pool — this affects the realistic magnitude of the attack and would benefit from direct inspection of the `UniversalCore`/pool deployment and liquidity parameters in a full Devin session.

### Citations

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

**File:** x/uexecutor/keeper/evm.go (L500-537)
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
```go
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

**File:** x/uexecutor/keeper/voting.go (L28-40)
```go
	// number of validators
	totalValidators := len(universalValidatorSet)

	// votesNeeded = ceil(2/3 * totalValidators)
	// >2/3 quorum similar to tendermint
	votesNeeded := (types.VotesThresholdNumerator*totalValidators)/types.VotesThresholdDenominator + 1

	k.Logger().Debug("voting on inbound ballot",
		"ballot_key", ballotKey,
		"validator", universalValidator.String(),
		"total_validators", totalValidators,
		"votes_needed", votesNeeded,
	)
```
