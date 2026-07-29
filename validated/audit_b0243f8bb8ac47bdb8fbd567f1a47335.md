### Title
Auto-swap gas-abstraction path derives its slippage bound (`minPCOut`) from the same manipulable Uniswap V3 quote it is meant to protect against - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
The external report describes a Curve `TwoWayLendingFactory` oracle that can be manipulated by an attacker via a direct transfer to the controller, and notes the "protection" (`check_lock`) does not cover this path because the manipulation channel bypasses it. The structural bug-class is: a price value read live from a manipulable on-chain source is used both to price an operation *and* to compute the safety bound for that same operation, so the bound moves in lockstep with the manipulation and provides no real protection.

The same structural pattern exists in Push Chain's `x/uexecutor` gas-abstraction (`GAS`/`FUNDS_AND_PAYLOAD` inbound swap) flow.

### Finding Description
When a `GAS`-type inbound is executed, `ExecuteInboundGas` fetches a live swap quote from the UniswapV3 `Quoter` contract at execution time and then uses that *same* quote, minus a fixed 5%, as the minimum-output slippage bound for the swap it immediately performs: [1](#0-0) 

```
quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
...
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

`quote` comes from `GetSwapQuote`, which calls into the on-chain UniswapV3 `Quoter`/pool for the `PRC20/WPC` pair — a pool whose spot price any unprivileged user can move with an ordinary swap or liquidity action, exactly like the Curve `price_oracle()` in the report was moved by a plain `transfer` to the controller. Because `minPCOut` is computed *from* the manipulated `quote` rather than from an independent, time-weighted, or externally-verified price, the 5% slippage guard only protects against divergence between the quote call and the swap call within the same atomic keeper execution (essentially zero, since both happen back-to-back inside one `DerivedEVMCall` sequence). It provides no protection against the attacker having already moved the pool price before the vote-finalization transaction that triggers this swap is processed. An attacker can skew the pool price downward immediately before (or in the same block as) a `MsgVoteInbound` that finalizes a `GAS`/`FUNDS_AND_PAYLOAD` inbound, causing the protocol's own auto-swap (`depositPRC20WithAutoSwap`, executed as the `uexecutor` module account) to execute at the manipulated rate while the "protection" bound is derived from that very same manipulated rate, then restore the pool price afterward and capture the difference.

### Impact Explanation
This directly affects the module-originated `DerivedEVMCall` swap path that converts a user's bridged PRC20 into WPC/PC for gas abstraction, using protocol/module-account funds. If exploitable, an attacker can extract value from the protocol's own swap execution (worse-than-fair-market exchange rate for the deposit-and-swap), i.e., unauthorized loss of protocol/user-controlled value during universal execution — squarely in the "corruption of PRC20 or native asset accounting" and "unauthorized module-originated EVM execution" impact categories.

### Likelihood Explanation
Uncertain/Medium. The trigger is fully unprivileged (any user can move the pool's spot price via a normal swap on the underlying AMM), and the vulnerable code path (`ExecuteInboundGas`) runs automatically whenever a `GAS`-type inbound reaches quorum — no special permission is required to cause an inbound to be processed. However, exploitability depends on factors not verifiable purely from the indexed code: pool liquidity depth (larger pools raise attack cost), whether the pool used is a low-liquidity, protocol-deployed pool (as suggested by e2e-tests comments about a "tiny WPC/pSOL AMM pool"), and whether validators batch/observe multiple blocks between quote and execution (widening the manipulation window). The 5% band does bound worst-case loss per swap to roughly the manipulation delta beyond 5%, so this is a bounded-value-leak rather than unbounded drain, and repeated exploitation would be needed for material impact.

### Recommendation
Do not derive the slippage floor from the same live on-chain price used to execute the swap. Use a more manipulation-resistant reference, e.g.:
- a time-weighted average price (TWAP) from the pool over multiple blocks instead of an instantaneous `Quoter` call, or
- an independently verified/oracle price (or the `ChainMeta`/registry-configured price) as the floor, with the live quote only used for execution routing, or
- widen liquidity depth checks / cap the max amount auto-swapped per block against a given pool, or
- require the swap's realized price to be within a bound of a TWAP reference rather than of itself.

### Proof of Concept
Conceptual (cannot be fully executed from the indexed code alone — the exact pool wiring/liquidity for the `WPC`/`PRC20` pair used by `GetUniversalCoreQuoterAddress`/`GetDefaultFeeTierForToken` was not fully retrievable from the index):
1. Attacker identifies the low-liquidity `WPC`/target-PRC20 UniswapV3 pool used for gas-abstraction auto-swaps (per e2e-tests comments, some pools are intentionally kept small on testnets, and mainnet liquidity for less common gas tokens could be thin).
2. Attacker swaps a sizeable amount against that pool to depress its spot price for `PRC20 -> WPC`.
3. Attacker (or a colluding relayer submitting the observation) times a `GAS`/`FUNDS_AND_PAYLOAD` inbound so that `MsgVoteInbound` reaches quorum while the pool is still skewed; `ExecuteInboundGas` calls `GetSwapQuote` against the skewed pool, computes `minPCOut = quote*0.95`, and executes `CallPRC20DepositAutoSwap` — the module-originated swap clears at the skewed price because the bound was derived from that same skewed price.
4. Attacker reverses their initial swap, restoring the pool and pocketing the difference between the fair price and the skewed price obtained by the protocol's auto-swap.

Given the uncertainty around actual pool liquidity/parameters (out of index scope), this should be verified with a live/staging environment (Devin session) before being treated as confirmed-exploitable; the structural analog to the reported bug class, however, is clearly present in this code path.

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
