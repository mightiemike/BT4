## Analysis

The last vote (`MsgVoteInbound`) that pushes an inbound's ballot over the `votingThreshold` finalizes the ballot and, inside the *same* `VoteInbound` state transition, calls `k.ExecuteInbound(ctx, utx)` synchronously [1](#0-0) , which for `GAS`/`GAS_AND_PAYLOAD` inbounds dispatches to `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap` [2](#0-1) .

Both paths fetch a live spot-price quote from the Uniswap V3 `QuoterV2.quoteExactInputSingle` and derive `minPCOut` as a flat 95% of that quote, then immediately execute `depositPRC20WithAutoSwap` against the same pool: [3](#0-2) [4](#0-3) [5](#0-4) 

The quote/swap pair executes in the same Cosmos-SDK transaction and same block as the whole `MsgVoteInbound` state transition, so it cannot be sandwiched by a *separate* mempool transaction inserted between the quote read and the swap. This is an architectural improvement over blind zero-slippage swaps. However, `quoteExactInputSingle` reads the pool's instantaneous spot price (`sqrtPriceX96`), not a time-weighted average — there is no TWAP or oracle cross-check anywhere in this flow, and the ABI confirms this is a plain spot-price quoter call [6](#0-5) . The same pattern repeats in the gas-refund path (`applyGasRefund` → `getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas`) [7](#0-6) [8](#0-7) .

This means an unprivileged attacker who can submit ordinary EVM transactions against the same Uniswap V3 `PRC20/WPC` pool (the pool is a normal, permissionlessly-tradeable AMM deployed as a universal system contract, per `x/uregistry` `SYSTEM_CONTRACTS`) can:
1. Push the pool's spot price against the pending inbound's swap direction in a transaction placed earlier in the same block (or an earlier block, since Cosmos-SDK block proposers can freely order transactions and there is no atomicity guarantee tying the manipulating trade and the module's auto-swap into one indivisible unit beyond "same tx as vote finalization" — the manipulating trade itself is a separate transaction the attacker fully controls the timing of).
2. Let the module's own logic read this manipulated spot price via `GetSwapQuote`, compute `minPCOut` as 95% of the already-depressed quote, and execute `depositPRC20WithAutoSwap` at that bad price.
3. Reverse the manipulating trade afterward, extracting value that comes directly out of the bridging user's received PC balance (the auto-swap output credited to the user's UEA).

This is a single-block/JIT price-manipulation variant of the reported bug class: rather than a user supplying `minAmountOut = 0`, the *module itself* computes an insufficiently-protected `minPCOut` from a manipulable spot price with only a flat 5% band, and does so on behalf of an ordinary bridging depositor who has no say in the parameter at all. On a thin-liquidity PRC20/WPC pool, 5% is easily exceeded by a modestly-capitalized attacker trading directly against Uniswap V3 concentrated liquidity.

### Caveats / what I could not verify
- I could not confirm the actual liquidity depth/configuration of deployed `PRC20/WPC` pools (this is operational/deployment data, not in the indexed code), which determines how easy a >5% price move is in practice.
- I could not fully verify whether block-proposer transaction ordering guarantees (e.g., whether `MsgVoteInbound` and ordinary EVM txs can be freely interleaved by the same block proposer, or whether there's a separate execution phase that limits this) — this affects exact attack timing precision, though the core issue (spot-price quote with no TWAP, only a flat 5% band, at module-forced-swap time) holds regardless.
- I did not find any TWAP-oracle or additional price-sanity-check code elsewhere in `x/uexecutor` or `x/uregistry` that might mitigate this.

### Title
Module-forced deposit auto-swap uses manipulable Uniswap V3 spot-price quote with only a flat 5% band, exposing bridging users to JIT price-manipulation losses - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`x/uexecutor` auto-swaps a bridging user's incoming PRC20 gas token into native PC by calling `depositPRC20WithAutoSwap`, computing the swap's `minPCOut` slippage floor from a live `QuoterV2.quoteExactInputSingle` spot-price quote with a hardcoded 5% band. This is analogous to the reported "swap executed without effective sandwiching protection" bug class: the protection value (`minPCOut`) is derived from a manipulable, TWAP-free spot price, so an unprivileged attacker who trades against the same pool immediately before the module's forced swap can force it to execute at a degraded price, extracting value from the bridging user's proceeds.

### Finding Description
`GetSwapQuote` reads `quoteExactInputSingle` from the Uniswap V3 `QuoterV2` contract — a pure spot-price read of the current pool tick/sqrtPrice, with no time-weighting [3](#0-2) . `ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` use this quote to compute `minPCOut = quote * 95 / 100` and immediately call `CallPRC20DepositAutoSwap` [9](#0-8) [10](#0-9) . The same pattern is used for gas refunds in `applyGasRefund`/`getSwapQuoteForRefund` [7](#0-6) .

Because the pool contract that the quoter/swap-router act on is a normal, permissionlessly tradeable AMM (deployed as one of the universal system contracts, see `UNIVERSAL_CORE` in `uregistrytypes.SYSTEM_CONTRACTS`), any unprivileged actor can submit ordinary EVM transactions to move its spot price. There is no minimum bridged-amount threshold, no TWAP guard, and no per-swap deadline enforcement (`deadline = 0` is passed explicitly, disabling that protection too) [11](#0-10) . The user whose inbound triggers this flow never supplies or reviews `minPCOut` — it's entirely computed by the module from a value the attacker can influence right before the module's own transaction lands.

### Impact Explanation
An attacker can degrade the exchange rate a bridging depositor receives when their gas-abstraction inbound is auto-swapped, siphoning value out of ordinary users' bridged funds via classic JIT/spot-price manipulation, up to the bound allowed by the flat 5% band (and potentially more if the true price move needed to breach the invariant requires manipulating just past the discount already baked in, on top of whatever slippage the pool's real liquidity permits). This falls within scope as "corruption of ... gas fee accounting, refund accounting ... native asset accounting" and value being drained from a user-facing flow reachable by an ordinary, unprivileged actor with no validator or admin compromise required.

### Likelihood Explanation
Likely low-to-medium in practice: it requires the pool for a given PRC20 to have thin enough liquidity that a realistic capital outlay can move price beyond 5%, and requires reasonably precise timing to land a manipulating trade in the same or adjacent block as a specific inbound's ballot finalization (attacker doesn't fully control exactly when the 2/3 threshold is reached, since that depends on independent Universal Validator votes). It is nonetheless a genuine, non-privileged, code-level gap: no user or module code anywhere checks for TWAP deviation or otherwise bounds the spot price used for `minPCOut`.

### Recommendation
Use a TWAP-based or otherwise manipulation-resistant price source (e.g., a longer observation window from the pool, or an external oracle) instead of the instantaneous `quoteExactInputSingle` spot quote when computing `minPCOut`, and consider widening/dynamically adjusting the slippage band based on measured pool liquidity/volatility rather than a flat 5%. Also set a real transaction deadline instead of `0` for `depositPRC20WithAutoSwap` and `refundUnusedGas` to prevent stale-quote execution far from the read time.

### Proof of Concept
1. Attacker identifies a `PRC20/WPC` Uniswap V3 pool used for gas-abstraction auto-swaps with modest liquidity.
2. Attacker submits (or has a pending) a `GAS`/`GAS_AND_PAYLOAD` bridging inbound; independently, or timed against an anticipated Universal Validator vote finalization, the attacker submits a large swap on the same pool that depresses the PRC20→WPC price.
3. Once the inbound's ballot finalizes, `VoteInbound` synchronously calls `ExecuteInboundGas`, which calls `GetSwapQuote` and reads the manipulated spot price, computes `minPCOut` at 95% of that depressed quote, and executes `depositPRC20WithAutoSwap` — crediting the bridging user (which may be the attacker themself, or a third-party victim if the attacker can predict/target the timing) with a materially worse PC amount than fair value.
4. Attacker reverses their manipulating trade, recouping the pool discount as profit extracted from the auto-swap's output.

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

**File:** x/uexecutor/keeper/execute_inbound.go (L18-29)
```go
	switch utx.InboundTx.TxType {
	case types.TxType_GAS: // fee abstraction
		return k.ExecuteInboundGas(ctx, *utx.InboundTx)

	case types.TxType_FUNDS: // synthetic
		return k.ExecuteInboundFunds(ctx, utx)

	case types.TxType_FUNDS_AND_PAYLOAD: // synthetic + payload
		return k.ExecuteInboundFundsAndPayload(ctx, utx)

	case types.TxType_GAS_AND_PAYLOAD: // fee abstraction + payload
		return k.ExecuteInboundGasAndPayload(ctx, utx)
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

**File:** x/uexecutor/types/abi.go (L459-485)
```go
const UNISWAP_QUOTER_V2_ABI = `[
  {
    "type": "function",
    "name": "quoteExactInputSingle",
    "inputs": [
      {
        "name": "params",
        "type": "tuple",
        "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
        "components": [
          { "name": "tokenIn",           "type": "address", "internalType": "address" },
          { "name": "tokenOut",          "type": "address", "internalType": "address" },
          { "name": "amountIn",          "type": "uint256", "internalType": "uint256" },
          { "name": "fee",               "type": "uint24",  "internalType": "uint24"  },
          { "name": "sqrtPriceLimitX96", "type": "uint160", "internalType": "uint160" }
        ]
      }
    ],
    "outputs": [
      { "name": "amountOut",                "type": "uint256", "internalType": "uint256" },
      { "name": "sqrtPriceX96After",        "type": "uint160", "internalType": "uint160" },
      { "name": "initializedTicksCrossed",  "type": "uint32",  "internalType": "uint32"  },
      { "name": "gasEstimate",              "type": "uint256", "internalType": "uint256" }
    ],
    "stateMutability": "nonpayable"
  }
]`
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

**File:** x/uexecutor/keeper/outbound.go (L259-269)
```go
// getSwapQuoteForRefund fetches a Uniswap quote for the gas token refund swap.
func (k Keeper) getSwapQuoteForRefund(ctx sdk.Context, gasToken common.Address, fee *big.Int, amount *big.Int) (*big.Int, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(ctx)
	if err != nil {
		return nil, err
	}
	wpcAddr, err := k.GetUniversalCoreWPCAddress(ctx)
	if err != nil {
		return nil, err
	}
	return k.GetSwapQuote(ctx, quoterAddr, gasToken, wpcAddr, fee, amount)
```
