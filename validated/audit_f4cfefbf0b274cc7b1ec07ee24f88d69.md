### Title
Auto-swap slippage bound derived from a same-transaction spot quote allows sandwich extraction of deposited GAS funds - ([File: x/uexecutor/keeper/execute_inbound_gas.go])

### Summary
`MsgVoteInbound` finalizes the inbound ballot and synchronously executes `GAS`/`GAS_AND_PAYLOAD` inbounds in the same message, converting the deposited external gas token into native PC via a Uniswap-V3-style swap on the `UniversalCore` contract. The `minPCOut` slippage floor is computed from a quote fetched with `GetSwapQuote` *at the moment of execution*, not from any manipulation-resistant reference such as a TWAP or a value supplied by the original inbound/user intent. This is functionally the same class of bug as the UniswapV3 locker report: a "protection" value is derived from spot state that the attacker can move immediately beforehand, so the check offers no real defense against sandwiching.

### Finding Description
`ExecuteInboundGas` (`x/uexecutor/keeper/execute_inbound_gas.go:104-153`) and `gasAndPayloadDepositAutoSwap` (`x/uexecutor/keeper/execute_inbound_gas_and_payload.go:347-378`) both:
1. Call `k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)` — a read-only `CallEVM` against the *current* pool state (`x/uexecutor/keeper/evm.go:500-538`).
2. Compute `minPCOut = quote * 95 / 100` (hardcoded 5% tolerance).
3. Immediately call `k.CallPRC20DepositAutoSwap(...)`, which performs the real swap against the same pool, enforcing only `minPCOut`.

Because the quote and the swap execute back-to-back inside the same keeper call (triggered synchronously by `VoteInbound` → `ExecuteInbound` when the inbound ballot crosses quorum, see `x/uexecutor/keeper/msg_vote_inbound.go:148-155`), the slippage floor is always computed *after* any price manipulation that already happened on-chain. An attacker who front-runs the finalizing `MsgVoteInbound` transaction (or any transaction landing in the same or an earlier block) with a large swap against the PRC20/WPC pool moves the pool price before `GetSwapQuote` is even called. The subsequent `minPCOut` check trivially passes because it is computed from the same manipulated price, not from a fair/undisturbed reference. The attacker then reverses their position (back-run), capturing the difference between the fair price and the manipulated execution price out of the user's deposited gas funds — the classic sandwich pattern the referenced UniswapV3 locker report describes, except here it is a bounded 5%-tolerance loop repeated on every GAS/GAS_AND_PAYLOAD inbound rather than a one-off liquidity-migration.

The same pattern exists in the outbound gas-refund path: `applyGasRefund` → `getSwapQuoteForRefund` (`x/uexecutor/keeper/outbound.go`), which also computes `minPCOut` from a same-call quote before calling `CallUniversalCoreRefundUnusedGas`.

### Impact Explanation
Any unprivileged external actor who can trade against the on-chain PRC20/WPC swap pool (a normal EVM interaction, no privileged role required) can extract value from ordinary users' cross-chain GAS deposits and gas refunds by sandwiching the deposit-triggered auto-swap. This is a direct, repeatable drain of user-controlled funds through the `x/uexecutor` universal execution path, corrupting the expected gas/PRC20 accounting for the deposit (the user receives materially less native PC than a fair swap would produce). This falls within the "corruption of ... gas fee accounting" and "draining ... of user ... funds" allowed-impact categories, reachable purely via honest-validator finalization of an ordinary user's inbound deposit — no malicious validator, relayer, or governance action needed.

### Likelihood Explanation
High likelihood: the trigger requires only (a) observing pending `MsgVoteInbound` transactions (or predicting block timing) and (b) executing a normal swap on the WPC/PRC20 pool before and after — a standard MEV/sandwich technique with no special access. The 5% tolerance is generous enough on typical pool depths to make repeated extraction profitable, and the pattern recurs on every GAS and GAS_AND_PAYLOAD inbound as well as every gas refund.

### Recommendation
Do not derive the slippage floor from a quote fetched in the same execution as the swap. Instead:
- Use a manipulation-resistant reference price (e.g., a TWAP from the pool, or a price bound sourced from chain-meta/oracle data already tracked by UVs) to compute `minPCOut`.
- Alternatively, have the original inbound/payload carry a user-specified `minOut`/max-slippage parameter (verified against a trusted reference) rather than trusting a spot quote taken immediately before the swap.
- Consider tightening the tolerance and/or adding circuit breakers (e.g., reject if quote deviates significantly from a recent TWAP) to reduce the extractable window.

### Proof of Concept
1. Attacker monitors the mempool/UV vote submissions for an imminent `MsgVoteInbound` that will finalize a `GAS` or `GAS_AND_PAYLOAD` inbound for token `X`.
2. Before that transaction lands, attacker submits a large swap on the `X`/`WPC` UniswapV3-style pool (via `UniversalCore`'s router) to move the spot price unfavorably for a subsequent `X → WPC` swap.
3. The finalizing `MsgVoteInbound` executes; `ExecuteInboundGas` calls `GetSwapQuote` which now returns a manipulated (low) output amount; `minPCOut = 95%` of that manipulated quote is trivially satisfied by `CallPRC20DepositAutoSwap`.
4. Attacker submits a back-run trade restoring the pool price, capturing the spread between the fair price and the manipulated execution price — funded by the value that should have gone to the user's UEA as native PC.

I could not verify the exact pool depth/liquidity parameters or whether any additional TWAP-based sanity check exists elsewhere in the `UniversalCore` Solidity contract (out of this repo's index), so the magnitude of extractable value per attack is not quantified here; a Devin session with full repo/contract access would be needed to confirm the absence of any contract-side TWAP guard. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** x/uexecutor/keeper/evm.go (L500-592)
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

**File:** x/uexecutor/keeper/outbound.go (L212-230)
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
