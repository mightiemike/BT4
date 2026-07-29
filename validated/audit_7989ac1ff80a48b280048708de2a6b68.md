### Title
Inbound `GAS` / `GAS_AND_PAYLOAD` autoswap slippage protection is anchored to a manipulable spot price, allowing sandwich extraction from user deposits - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/evm.go)

### Summary
The external report describes an attacker front-running a protocol deposit/conversion (`GMigration.prepareMigration`) by manipulating the price used to convert deposited assets into shares, so the deposit is credited far less value than it should be. The Push Chain analog is the `GAS` and `GAS_AND_PAYLOAD` inbound execution paths, which auto-swap a user's minted PRC20 into native PC through the on-chain Uniswap V3 pool using a spot quote fetched at execution time, with only a fixed 5% slippage buffer computed from that same (potentially attacker-manipulated) spot quote.

### Finding Description
When a `GAS` or `GAS_AND_PAYLOAD` inbound reaches UV quorum, `ExecuteInboundGas` / `ExecuteInboundGasAndPayload` compute the swap's minimum-out entirely from a live spot quote: [1](#0-0) [2](#0-1) 

`GetSwapQuote` calls Uniswap V3 `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96 = 0` (no price-limit bound), which returns the amount out based on the pool's instantaneous reserves/sqrtPrice at call time — there is no TWAP or any independent oracle price used anywhere in this path. `minPCOut` is then derived purely as `quote * 95 / 100`: [3](#0-2) 

The quote-fetch and the swap-execution (`CallPRC20DepositAutoSwap`) both occur inside the same keeper call, so the 5% buffer only protects against price impact caused by the deposit's *own* trade — it does nothing to protect against the pool having already been pushed to an unfavorable price by an attacker's prior transaction. An unprivileged attacker holding the counter-asset can:
1. Observe (in the mempool / block-building window) that a `MsgVoteInbound` reaching quorum for a `GAS`/`GAS_AND_PAYLOAD` inbound is about to execute (vote messages are public, gasless, and broadcast by UVs; a user's own `MsgExecutePayload` gasless flow is likewise public before inclusion).
2. Submit a large trade against the same low-liquidity WPC/PRC20 pool immediately before that transaction lands in the same block (front-run), pushing the spot price against the deposit.
3. Let the deposit's autoswap execute — `GetSwapQuote` now reflects the manipulated price, so `minPCOut` is computed from an already-bad price and the swap still "passes" its own slippage check.
4. Reverse the trade after the deposit executes (back-run), realizing a profit extracted from the value that should have gone to the depositing user's PRC20→PC conversion.

This is structurally the same failure mode as the Gro `GMigration` bug: a conversion/minting step whose fairness depends on a spot price that an unprivileged party can manipulate immediately beforehand, with the "protection" (5% tolerance in Push Chain; deposit-rounding in Gro) bounding only the wrong thing.

### Impact Explanation
A successful sandwich extracts value from the PRC20→PC conversion that is credited to the recipient's UEA on a `GAS`/`GAS_AND_PAYLOAD` inbound. Because the slippage floor is derived from the same manipulated spot price rather than an independent/TWAP reference, the loss is not capped at a genuine 5% versus fair value — it is capped at 5% versus whatever price the attacker has already pushed the pool to, which in a thin-liquidity pool (as used for early PRC20/WPC pairs) can be a large fraction of the deposited value. This is a theft of user-deposited value reachable by any unprivileged actor with capital to trade against the pool; no validator, admin, or key compromise is required.

### Likelihood Explanation
Medium. It requires: (a) a WPC/PRC20 pool with limited liquidity relative to the deposit size (plausible for a newly-launched Push Chain PRC20 market, mirroring the "newly deployed vault with no supply" precondition in the original bug), and (b) the ability to place a transaction immediately before the finalizing `MsgVoteInbound` (or `MsgExecutePayload`) in the same or an adjacent block. Whether Push Chain's mempool ordering allows reliable positioning (priority-by-gas vs FIFO) could not be conclusively determined from the indexed code; this affects only the reliability of achieving optimal sandwich timing, not the existence of the underlying unprotected-spot-price design flaw.

### Recommendation
- Do not derive `minPCOut` solely from a spot `quoteExactInputSingle` call taken immediately before the swap. Use a TWAP-based reference price (e.g., Uniswap V3 `observe`) or an independent price oracle to bound the acceptable execution price, and only allow a small band (e.g., 1-2%) around that reference — not around whatever the pool currently reports.
- Consider setting a non-zero `sqrtPriceLimitX96` bound tied to the reference price so the underlying Uniswap V3 swap itself reverts if the pool is meaningfully away from the reference, independent of the quoter's return value.
- For low-liquidity pools, consider a maximum swap-size cap or dynamic slippage bound proportional to observed pool depth to reduce the value at risk from a single sandwich.

### Proof of Concept
Conceptual (cannot be fully executed without a live pool/mempool):
1. Attacker holds WPC and PRC20-X, and the WPC/PRC20-X pool used by `GetDefaultFeeTierForToken` / `GetUniversalCoreQuoterAddress` has thin liquidity.
2. A user submits a source-chain deposit that becomes a `GAS_AND_PAYLOAD` inbound; UVs vote and the transaction finalizing quorum is visible before inclusion.
3. Attacker submits a large PRC20-X → WPC (or WPC → PRC20-X) swap positioned immediately before the finalizing vote transaction in the same block, moving the pool's spot price unfavorably for the upcoming autoswap direction.
4. The `MsgVoteInbound` executes `ExecuteInboundGasAndPayload` → `gasAndPayloadDepositAutoSwap` → `GetSwapQuote` returns a quote reflecting the manipulated price → `minPCOut = quote * 0.95` is still satisfied by the actual swap, so the deposit's autoswap executes at the bad price.
5. Attacker reverses their trade, capturing the price difference at the depositing user's expense. [4](#0-3)

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
