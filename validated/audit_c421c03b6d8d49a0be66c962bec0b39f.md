### Title
Spot-price gas-abstraction swap in `ExecuteInboundGas` lets an attacker manipulate the WPC/PRC20 pool price to steal value from bridging users - (File: `x/uexecutor/keeper/execute_inbound_gas.go`)

### Summary
The H-1 report describes an attacker manipulating a conversion ratio (`pricePerShare`) that the protocol trusts to compute value for other users, causing loss due to unprotected reliance on a manipulable on-chain ratio. The closest reachable analog in Push Chain's scoped `x/uexecutor` code is the gas-abstraction auto-swap path: `ExecuteInboundGas` computes a swap output using a live Uniswap-V3-style spot quote (`GetSwapQuote`) and applies only a fixed 5% slippage tolerance, then immediately executes the swap through `CallPRC20DepositAutoSwap`. Both the "quote" and the "swap" derive from the same manipulable on-chain AMM reserves, with no TWAP or external price cross-check.

### Finding Description
`ExecuteInboundGas` [1](#0-0)  is invoked synchronously as part of ballot finalization when a `MsgVoteInbound` reaches quorum (a fully user/attacker-observable, public mempool event). It performs:

1. `GetSwapQuote` — a `CallEVM` (commit=false) read of the Uniswap V3 `QuoterV2.quoteExactInputSingle`, which reflects the *current* pool reserves/spot price. [2](#0-1) 
2. A hardcoded `minPCOut = quote * 95 / 100` slippage bound. [3](#0-2) 
3. `CallPRC20DepositAutoSwap` (commit=true) which actually executes the deposit + swap against the same pool. [4](#0-3) 

Because the quote is derived from the pool's spot price at execution time (not a time-weighted average or any externally-anchored price), and only a static 5% tolerance is enforced, an attacker who observes the pending finalizing vote transaction in the mempool can:
- Front-run it with a large swap against the same WPC/PRC20 pool to push the spot price to the edge of the 5% band, and
- Back-run it with the reverse swap to capture the arbitrage,

extracting value from the module's swap that was meant to convert the bridging user's incoming PRC20 into native PC for gas. This is directly analogous to the reported bug class: an unprivileged party manipulates an on-chain conversion ratio that the protocol trusts unconditionally for computing another (unrelated, future) user's outcome, and profits at that user's expense.

### Impact Explanation
Every inbound `FUNDS` transaction (or funds+payload transaction requiring gas abstraction) that goes through `ExecuteInboundGas` is exposed. Since the swap size and slippage tolerance are fixed and predictable from public inbound data, and the triggering vote transaction is visible before execution, this is a repeatable, low-cost attack. Funds lost are drawn from user-bridged assets being converted to gas, i.e. corruption of gas-fee/asset accounting for genuine bridging users, falling under the "corruption of ... gas fee accounting" and "PRC20 or native asset accounting" allowed-impact categories.

### Likelihood Explanation
Likelihood is moderate-to-high: it requires no privileged access, only capital to move the specific PRC20/WPC pool and the ability to time transactions around the observable vote-finalizing transaction (or any transaction that triggers the swap) in the same or an adjacent block — standard MEV/sandwich capability on an EVM-compatible chain. Liquidity depth of the specific pool determines the attacker's capital requirement and thus the practical severity.

### Recommendation
- Replace the spot-price `QuoterV2` read with a TWAP-based quote (or a governance/oracle-provided reference price) before computing `minPCOut`.
- Alternatively, size the slippage tolerance dynamically based on trade size relative to pool liquidity, and/or cap the maximum swap size per inbound to limit exploitability.
- Consider protecting the finalizing vote/execution path from being front-run predictably, e.g., by not exposing that a specific swap of a specific size is about to execute until it is atomic with the read.

### Proof of Concept
1. Attacker monitors the mempool/chain for `MsgVoteInbound` transactions that will reach the 2/3 threshold and trigger `ExecuteInboundGas` for a known token/amount (inbound data, including `AssetAddr` and `Amount`, is public per the `Inbound` struct).
2. Attacker submits a transaction (with sufficient gas/priority to land before the finalizing vote in the same block, or in an earlier block if pool state persists) that swaps a large amount into/out of the WPC/PRC20 pool used by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`, shifting the spot price to the edge of the fixed 5% band.
3. The finalizing vote executes `ExecuteInboundGas` → `GetSwapQuote` reads the now-skewed spot price → `CallPRC20DepositAutoSwap` executes at the unfavorable rate within the 5% tolerance, sending the bridging user less PC than fair value.
4. Attacker back-runs with the reverse swap, capturing the price reversion and the value difference extracted from the module's swap.

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
