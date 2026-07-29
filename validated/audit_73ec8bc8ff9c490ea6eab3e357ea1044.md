The strongest analog to the reported "unenforced slippage" issue is in the auto‑swap step of Push Chain's inbound-gas execution flow, not in the ballot/registry/precompile layers.

### Title
Auto-swap `minPCOut` slippage floor is computed from the same manipulable spot quote it is meant to protect against - ([File: x/uexecutor/keeper/evm.go](x/uexecutor/keeper/evm.go))

### Summary
When an inbound `GAS` or `GAS_AND_PAYLOAD` transaction is executed, `x/uexecutor` swaps the deposited PRC20 for WPC through the on-chain UniversalCore/Uniswap-V3-style router. The minimum-output guard (`minPCOut`) is derived as a fixed 5% haircut off a spot quote fetched from the pool at the moment of execution, with no reference to an external/TWAP price and no user- or governance-configurable bound. This mirrors the audited "slippage not enforced" bug class: the check exists syntactically but bounds the swap against a value an attacker can move beforehand, so it provides no real protection.

### Finding Description
`GetSwapQuote` performs a live, single-block spot-price read via `quoteExactInputSingle` against the Uniswap V3-style Quoter, and the caller immediately derives `minPCOut = quote * 95 / 100`: [1](#0-0) [2](#0-1) 

The same pattern is repeated in `gasAndPayloadDepositAutoSwap` and in the gas-refund swap path: [3](#0-2) [4](#0-3) 

This execution runs synchronously inside `MsgVoteInbound` handling once a ballot finalizes (2/3 of honest Universal Validators voted the same inbound) — it is not deferred to a privileged BeginBlock/EndBlock step insulated from ordinary transactions: [5](#0-4) 

Because `quote` and `minPCOut` are both computed from the pool's current reserves at the moment this transaction lands, an unprivileged attacker who can also submit ordinary swap transactions against the same on-chain PRC20/WPC pool can:
1. Push the pool price against the pending deposit's direction with a large swap in a preceding transaction (same block or immediately prior),
2. Let the deposit-triggered auto-swap execute against that skewed price — its own "protection" (95% of quote) is 95% of the *already manipulated* quote, so it does not stop the loss,
3. Reverse the price-moving trade afterward, extracting value that the depositing user should have received.

This is the same root cause as the reported bug: a slippage bound exists in name only because it isn't tied to any threshold independent of attacker-controlled inputs (in the original report, the caller passed extreme raw values with no clamp; here, the "bound" is silently rebased onto attacker-influenced pool state before being applied).

### Impact Explanation
A successful sandwich degrades the amount of PC/WPC a legitimate cross-chain depositor's UEA receives from the protocol-run auto-swap, i.e., corruption of PRC20/native asset accounting and unauthorized value extraction from a user's inbound deposit — squarely in the "corruption of ... native asset accounting" and "draining ... of user ... funds" impact categories, without requiring any privileged actor (validators/UVs behave honestly; only an ordinary EVM user manipulating the DEX pool is needed).

### Likelihood Explanation
Requires only standard EVM transactions against the on-chain WPC/PRC20 pool and normal mempool/ordering control that any unprivileged user has (no compromise of validators, TSS, or governance needed). Because Push Chain's EVM block production is otherwise standard CometBFT/Cosmos, an attacker with capital to move the target pool's reserves can reliably time this around the votes that finalize an inbound ballot.

### Recommendation
Do not derive `minPCOut` purely from a spot quote fetched immediately before the swap. Options: use a time-weighted average price (TWAP) from the pool, enforce a maximum single-block price-impact/deviation check against a recent on-chain oracle price, or add pool-manipulation guards (e.g., reject if pool price deviated more than X% from a recent moving average since the inbound was observed). At minimum, make the tolerance configurable via governance and validate it against an independent reference price rather than the value being protected against.

### Proof of Concept
1. Attacker observes a pending inbound GAS/GAS_AND_PAYLOAD deposit about to be finalized (visible once 2 of 3 UV votes for the ballot are in — the last vote is public mempool activity).
2. Before/alongside the final `MsgVoteInbound` that triggers `ExecuteInboundGas`, attacker submits a large swap on the PRC20/WPC pool in the same direction as the impending deposit-triggered swap, moving the spot price unfavorably for the depositor.
3. The finalizing vote executes `GetSwapQuote` → `minPCOut = quote*95/100` against the now-skewed pool state (`x/uexecutor/keeper/execute_inbound_gas.go:134-148`), then calls `CallPRC20DepositAutoSwap`, which executes at the bad price since `minPCOut` was computed from that same bad price.
4. Attacker reverses their swap, realizing the difference extracted from the depositor's proceeds.

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
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
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-378)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L213-230)
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
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L66-87)
```go
	}

	// Step 3: Vote on inbound ballot (uses the original inbound data as-is for the ballot key,
	// so UVs that observe different field data will correctly produce different votes)
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
	if err != nil {
		return errors.Wrap(err, "failed to vote on inbound ballot")
	}

	commit()

	// Voting not finalized yet
	if !isFinalized {
		k.Logger().Debug("vote inbound recorded, ballot not yet finalized",
			"validator", universalValidator.String(),
			"utx_key", universalTxKey,
		)
		return nil
	}

	// --- Ballot finalized: always create UTX from here on ---
	k.Logger().Info("inbound ballot finalized, creating utx", "utx_key", universalTxKey, "source_chain", inbound.SourceChain)
```
