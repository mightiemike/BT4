## Analog Found: Same-block Uniswap V3 spot-price self-reference used to bound the gas-abstraction PRC20→WPC swap

### Title
Gas-abstraction swap uses a manipulable Uniswap V3 spot quote as its own slippage bound, allowing an unprivileged attacker to sandwich `ExecuteInboundGas` and drain value from the PRC20/WPC pool - (File: `x/uexecutor/keeper/execute_inbound_gas.go`)

### Summary
The external report's root cause is that a Uniswap pool's price can be set/observed by an attacker just before value is committed against it, so the check meant to protect the operation ends up being computed from attacker-controlled state. The direct Push Chain analog is `Keeper.ExecuteInboundGas`, which computes the `minPCOut` slippage floor for the PRC20→WPC swap from a live call to `QuoterV2.quoteExactInputSingle` (`GetSwapQuote`), i.e. the *current spot price of the same pool* the swap will execute against, with only a fixed 5% cushion.

### Finding Description
When a `GAS`-type inbound is finalized, `ExecuteInboundGas` fetches a swap quote and immediately executes the swap against the same pool with a bound derived from that very quote: [1](#0-0) 

The quote is obtained via `GetSwapQuote`, a raw call to Uniswap's `QuoterV2.quoteExactInputSingle`, which reflects the pool's current reserves/tick — i.e., whatever price an attacker last left it at: [2](#0-1) 

The bound is then computed as a flat 95% of that just-read quote, with no TWAP, no oracle cross-check, and no minimum-liquidity/price-deviation guard:
```
minPCOut := quote * 95 / 100
``` [3](#0-2) 

Because the bound is self-referential (derived from the same manipulable pool state it is meant to protect against), this is architecturally the same failure mode as the Uniswap report: a price check that is supposed to guard fund movement is instead computed from state an unprivileged actor can move. Any ordinary user who can submit a transaction on Push Chain's EVM (an unprivileged, permissionless action — swapping in the PRC20/WPC pool is not gated by any admin, validator, or TSS role) can push the pool's spot price down immediately before the block in which validators' `MsgVoteInbound` votes reach quorum and trigger `ExecuteInboundGas`, then swap back afterward. The 5% tolerance only protects against benign price drift between quote and execution, not against an attacker who controls both the "before" state and the trade itself.

### Impact Explanation
Every `GAS`-type inbound (used for gas abstraction — depositing a user's bridged asset and auto-swapping part of it into native WPC to fund their UEA) is exposed. An attacker can extract value from the PRC20/WPC pool by sandwiching this swap: buy WPC cheap right before the module's swap executes at depressed PRC20 price, then sell back after. The victim (the depositing user, and indirectly the pool's LPs / protocol-owned liquidity) receives materially less WPC than the honest market price would produce — a real, unauthorized value transfer out of protocol/user-controlled liquidity, reachable by an ordinary user with no privileged role, matching the in-scope "corruption of ... gas fee accounting ... token mapping" and "stealing ... user or protocol-controlled funds" categories.

### Likelihood Explanation
Likelihood is high for a validator/searcher able to observe the mempool or predict which block will finalize a pending `MsgVoteInbound` quorum (the last vote is a public, unprivileged transaction whose timing/content is visible before inclusion). No special access is required — only the ability to submit ordinary swap transactions against the PRC20/WPC pool and to time them relative to the finalizing vote transaction, both of which are available to any unprivileged user of Push Chain.

### Recommendation
Do not derive the slippage bound from a live, attacker-influenceable spot quote of the same pool used for execution. Use a manipulation-resistant reference price instead — e.g., a TWAP over a meaningful window, an external/oracle price feed, or a protocol-configured maximum-deviation guard compared against a longer-window average — and reject/queue the swap if the live quote deviates beyond that bound rather than using the live quote as its own floor. Additionally consider capping per-block swap size for module-originated auto-swaps or routing gas-abstraction swaps through liquidity pools with anti-manipulation safeguards.

### Proof of Concept
Conceptual PoC (cannot be executed without a live Push Chain devnet + Uniswap V3 pool deployment, but the mechanics mirror the original report's PoC):
1. Attacker observes a pending `GAS`-type inbound whose quorum-completing `MsgVoteInbound` is about to land in block N (validator votes and their content are public before finalization).
2. In block N (or the block immediately preceding, depending on mempool ordering), attacker submits a large `exactInputSingle` swap on the PRC20/WPC Uniswap V3 pool used by `UNIVERSAL_CORE`, pushing the PRC20→WPC price down.
3. `ExecuteInboundGas` executes in the same/next block: `GetSwapQuote` reads the depressed price, computes `minPCOut = quote * 0.95`, and `CallPRC20DepositAutoSwap` executes at that depressed price — the victim's UEA receives far less WPC than fair value.
4. Attacker reverses the trade (sell WPC back for PRC20), capturing the price difference, net of Uniswap fees.

This mirrors the original report precisely: the check meant to protect the honest party (target price / minOut) is derived from state the attacker just set, so "not all the value goes where intended" and the attacker "steals a large amount" of the pooled asset via a sandwich rather than direct theft.

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
