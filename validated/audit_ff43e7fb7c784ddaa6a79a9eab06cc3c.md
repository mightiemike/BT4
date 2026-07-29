## Finding

### Title
Fixed 5% Auto-Swap Slippage Tolerance in Inbound Gas Deposit Processing Enables Sandwich-Attack Value Extraction - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go)

### Summary
The external report flags `MAX_PRICE_DEVIATION_UPPER_BOUND = 500` (5%) as an unjustifiably wide tolerance that lets manipulated oracle prices distort stablecoin minting economics. The same invariant class — "a wide, hardcoded percentage tolerance around a market-derived price used to move real value" — recurs natively in Push Chain's universal execution path: the gas-abstraction auto-swap performed when processing `GAS` and `GAS_AND_PAYLOAD` inbound deposits hardcodes a 5% slippage tolerance (`minPCOut = quote * 95 / 100`) with no per-pool or per-asset configurability, no minimum liquidity/TWAP protection, and no cap tied to deposit size.

### Finding Description
When a validator quorum finalizes a `GAS` or `GAS_AND_PAYLOAD` inbound (the default path for cross-chain gas top-ups), `ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` fetch a spot quote from `QuoterV2.quoteExactInputSingle` and immediately execute a Uniswap-V3-style swap with `minPCOut` fixed at 95% of that quote: [1](#0-0) [2](#0-1) 

The quote itself is fetched via `GetSwapQuote` (a `commit=false` EVM call against the on-chain pool state at the time the finalizing `MsgVoteInbound`/`MsgExecutePayload` transaction executes): [3](#0-2) 

There is no per-asset or per-pool slippage configuration (unlike the audited `Engine.sol` which at least allows `assetConfigs[asset].maxDeviation` to be tuned down); the 5% figure is a single hardcoded constant applied uniformly to every PRC20/WPC pool regardless of liquidity depth, deposit size, or asset type. Because the pool price used for the quote is read from live on-chain AMM state at execution time (which is driven by ordinary, unprivileged swap transactions against the same pool), an attacker can move the pool price up to the slippage boundary immediately before the deposit-triggering transaction executes and reverse the trade afterward, extracting value from the swap that would otherwise have gone to the depositing user/protocol.

### Impact Explanation
Every automatic gas-abstraction swap (triggered by ordinary cross-chain deposits — no privileged role required to originate an inbound) can lose up to ~5% of the swapped principal to an attacker capable of moving the WPC/PRC20 pool price within that band, particularly on lower-liquidity pools where 5% price impact is cheap to produce. This directly corrupts PRC20/native asset accounting for the affected `UniversalTx` (the recipient UEA receives materially less WPC/PRC20 than the deposit should have converted to), which falls within the "corruption of PRC20 or native asset accounting" and "unauthorized ... release" impact categories for this scope.

### Likelihood Explanation
Reachable by any unprivileged actor: sending a cross-chain deposit is the default, permissionless user flow, and pool price can be moved by anyone submitting ordinary swap transactions against the pool — no compromise of validators, TSS, or admin keys is required. The main constraint is the attacker's ability to time their manipulating trades around the specific block in which the finalizing inbound vote/execution lands, and the depth/liquidity of the specific PRC20/WPC pool, which determines how cheap a 5% price move is to produce.

### Recommendation
- Replace the fixed 95% (5%) constant with a configurable, asset/pool-specific slippage bound stored in the token/registry config (analogous to the report's recommendation to set per-asset deviation ranges), defaulting to a much tighter value (e.g., 25–100 bps) for high-liquidity pairs.
- Consider deriving `minPCOut` from a time-weighted average price (TWAP) rather than a single spot quote fetched moments before execution, to reduce single-block manipulation exposure.
- Add a circuit breaker: if the quoted price deviates beyond a safe bound from a recent TWAP/reference price, skip the auto-swap and simply deposit the PRC20 without converting, rather than force a lossy swap.

### Proof of Concept
1. Attacker identifies a PRC20/WPC pool used for gas-abstraction auto-swaps with moderate liquidity.
2. Attacker monitors the mempool/upcoming blocks for a `MsgVoteInbound` (or similar) transaction that will finalize quorum on an inbound `GAS`/`GAS_AND_PAYLOAD` deposit for that PRC20 asset.
3. Immediately before that transaction is included, attacker submits a swap that pushes the pool price against the pending deposit direction by close to 5% (bounded by `minPCOut = quote*95/100` in `execute_inbound_gas.go`/`execute_inbound_gas_and_payload.go`).
4. The inbound-processing swap executes at the manipulated price, converting the deposit at up to ~5% below fair value; the shortfall accrues to the pool (i.e., to the attacker as LP/arbitrageur) instead of the depositing user.
5. Attacker reverses their initial trade in a subsequent transaction, restoring the pool price and realizing the extracted value, net of pool fees.

### Citations

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
