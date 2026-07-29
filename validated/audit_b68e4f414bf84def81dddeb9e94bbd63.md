## Title
Gas-abstraction auto-swaps price PRC20→WPC conversions off manipulable Uniswap V3 spot quotes, enabling MEV sandwich extraction from every bridged deposit — (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The Notional report shows that valuing an asset from *current* AMM reserves (a spot price) rather than a manipulation-resistant TWAP lets an attacker flash-manipulate the reserves and profit at the protocol's expense. Push Chain's gas-abstraction flow reproduces the same anti-pattern: every inbound bridge deposit that needs gas is auto-swapped PRC20→WPC using a slippage bound (`minPCOut`) derived from a live Uniswap V3 `QuoterV2.quoteExactInputSingle` call, which reads the pool's current spot reserves.

### Finding Description
`GetSwapQuote` calls `quoteExactInputSingle` on the configured Uniswap V3 quoter to price `amount` of PRC20 into WPC using the pool's current tick/liquidity state: [1](#0-0) 

That spot quote is then given only a flat, fixed 5% slippage collar and passed straight into the actual swap: [2](#0-1) 

The identical pattern (`quote * 95 / 100`) is repeated for `GAS_AND_PAYLOAD` inbound processing: [3](#0-2) 

and again for the excess-gas refund path executed on successful outbound observation: [4](#0-3) 

All three call sites feed the quote-derived `minPCOut` into `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`, which perform the real Uniswap V3 swap via `DerivedEVMCall`: [5](#0-4) 

This flow is triggered deterministically and predictably: it fires as soon as an inbound ballot in `x/uvalidator` crosses the 2/3 voting threshold via `VoteInbound` / `VoteOnInboundBallot`: [6](#0-5) 

Because validator votes are ordinary, publicly-broadcast Cosmos transactions sitting in the mempool before inclusion, an unprivileged attacker (an ordinary user who deposited funds through the gateway and is simply waiting for their own inbound to finalize) can observe the pending finalizing vote transaction and sandwich it:
1. Attacker swaps a large amount in the same PRC20/WPC Uniswap V3 pool right before the finalizing vote transaction in the same block, pushing the spot price away from fair value.
2. The finalizing vote transaction executes, triggering `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`, which fetches `GetSwapQuote` at the now-manipulated spot price and executes the deposit-auto-swap within only a 5% band of that bad price.
3. Attacker reverses their manipulation swap immediately after in the same block, capturing the spread the module's swap left on the table.

This is the same "spot-price-as-oracle" footgun described in the source report: no TWAP or manipulation-resistant price is used, and the flat 5%-of-spot-quote bound provides no real protection against a large, directed swap since the "reference" price itself is the thing being manipulated.

### Impact Explanation
Every gas-abstraction auto-swap (on inbound execution and on outbound gas refunds) can be MEV-sandwiched to bleed value out of the amounts being swapped for users' UEAs/recipients, i.e., unauthorized value extraction from user/protocol-controlled funds flowing through the universal execution path — squarely in-scope under "corruption of PRC20 or native asset accounting … token mapping" and "unauthorized … release … of user or protocol-controlled funds." The magnitude scales with the depth/liquidity of the specific PRC20/WPC pool and the size of the bridged deposit; thinner pools (newly listed PRC20 tokens) are especially exposed since 5% of a heavily-skewed spot price can still be a large absolute loss.

### Likelihood Explanation
No privileged role is required — the attacker only needs to be an ordinary bridging user (or any mempool-watching third party) who can submit ordinary swap transactions against the same on-chain Uniswap V3 pool that `UniversalCore` uses, timed around the publicly-visible finalizing vote transaction. This requires no validator collusion, no flashloan even (attacker can use their own capital across the sandwich, or a flashloan for larger effect), and can be repeated on every inbound deposit and every outbound gas refund that goes through the auto-swap path.

### Recommendation
Do not derive `minPCOut` purely from a same-block spot quote. Options:
- Use a TWAP-based reference price (e.g., Uniswap V3 `observe`) for the "fair value" reference instead of `quoteExactInputSingle`'s instantaneous quote, keeping the slippage band as protection against normal price movement rather than as the sole defense against manipulation.
- Enforce a maximum deviation between the on-chain TWAP and the spot quote before allowing the auto-swap to proceed, reverting to a safer no-swap deposit path (as already exists) if deviation exceeds a threshold.
- Consider routing gas-abstraction swaps through a mechanism less sensitive to per-block manipulation (e.g., batched/delayed execution, or capping swap size relative to pool depth).

### Proof of Concept
1. Attacker deposits an inbound (GAS or GAS_AND_PAYLOAD type) through the gateway on an external chain for a PRC20 with a moderately liquid pool on Push Chain.
2. Attacker monitors the Push Chain mempool for the `MsgVoteInbound` transaction that will push the ballot over the 2/3 threshold and finalize the attacker's inbound (`VoteOnInboundBallot`, `x/uexecutor/keeper/voting.go`).
3. Attacker submits (with higher priority) a large swap in the PRC20/WPC Uniswap V3 pool to depress the spot price just before that finalizing vote transaction lands in the same block.
4. The finalizing vote transaction executes `ExecuteInboundGas`, which calls `GetSwapQuote` (reads the now-manipulated spot price) and `CallPRC20DepositAutoSwap` with `minPCOut = quote * 95%`, executing the deposit-swap at the manipulated rate.
5. Attacker submits a third transaction reversing their initial manipulation swap, realizing arbitrage profit extracted from the spread the module's auto-swap left behind — all confirmed in the same block.

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

**File:** x/uexecutor/keeper/voting.go (L48-58)
```go
	// Step 2: Call VoteOnBallot for this inbound synthetic
	_, isFinalized, isNew, err = k.uvalidatorKeeper.VoteOnBallot(
		ctx,
		ballotKey,
		uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		universalValidator.String(),
		uvalidatortypes.VoteResult_VOTE_RESULT_SUCCESS,
		universalValidatorSetStrs,
		int64(votesNeeded),
		int64(types.DefaultExpiryAfterBlocks),
	)
```
