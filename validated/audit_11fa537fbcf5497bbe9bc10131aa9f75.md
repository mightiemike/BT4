### Title
Unprotected spot-price quoting in inbound auto-swap flows allows sandwich extraction of user/protocol funds - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/outbound.go)

### Summary
Push Chain's inbound-deposit auto-swap path (and the outbound gas-refund swap path) prices PRC20→WPC conversions using a single, same-block spot quote from the on-chain Uniswap-style Quoter contract, then applies only a fixed 5% slippage bound (`minPCOut = quote * 95 / 100`) computed from that same manipulable quote. This is the same class of bug as the FraxCrossChainFarmSushi report: a value derived from live, attacker-influenceable pool state (Uniswap spot price / quoter output) is used directly as the basis for an economically important calculation (there, veFXS boost; here, the guaranteed minimum output of a protocol-executed swap), with no TWAP or external price check to resist manipulation.

### Finding Description
When an inbound deposit is processed with `IsCEA`/gas+payload flows, `gasAndPayloadDepositAutoSwap` (x/uexecutor/keeper/execute_inbound_gas_and_payload.go:348-379) and the analogous helper in `execute_inbound_gas.go` fetch a swap quote via `k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)` and then compute:
```go
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
``` [1](#0-0) 

The same pattern is used for gas refunds in `getSwapQuoteForRefund` / `applyGasRefund`: [2](#0-1) 

`GetSwapQuote` is implemented against a live Uniswap-style Quoter contract (`x/uexecutor/keeper/evm.go`), i.e., it reads the *current* pool reserves/tick state rather than a time-weighted average. Because the quote and the slippage floor are both derived from the same instantaneous pool state at execution time, and because pool state on Push Chain (WPC/PRC20 pools) is itself mutable by ordinary unprivileged EVM transactions, an attacker can:

1. Submit an ordinary EVM transaction that swaps a large amount through the relevant WPC/PRC20 pool to move its spot price in a chosen direction.
2. Trigger (or wait for) their own inbound deposit to be finalized and executed by `ExecuteInboundGasAndPayload`, which fetches the quote and executes the auto-swap using `minPCOut` derived from the now-skewed price.
3. Immediately reverse the initial swap to restore the pool price, capturing the difference between the manipulated `minPCOut` and the true market rate as profit, extracted from protocol/user funds since the deposited PRC20 is converted at an off-market rate enforced by the protocol's own swap call.

The 5% slippage tolerance is a fixed static bound, not derived from any external/canonical price or TWAP, so it does not defend against this manipulation — it only bounds normal price movement, not attacker-induced movement. This directly matches the reported bug class: a spot-price-dependent value (there, `minVeFXSForMaxBoost`; here, `minPCOut`) is trusted for an economically consequential action without resistance to manipulation.

### Impact Explanation
This falls within the allowed impact of "corruption of PRC20 or native asset accounting" and "stealing/draining of user or protocol-controlled funds," since the auto-swap executes with attacker-favorable (or protocol-unfavorable) pricing baked into the on-chain state transition that the module itself performs on behalf of the user/protocol, with no external validation of fairness beyond the vulnerable quote.

### Likelihood Explanation
Exploitability depends on: (a) actual liquidity depth of the WPC/PRC20 pools deployed on Push Chain, (b) whether the attacker can reliably time their own inbound's processing relative to their manipulation trades, and (c) whether `fee` (pool tier) selection via `GetDefaultFeeTierForToken` picks a low-liquidity pool that is cheap to move. I was not able to fully verify pool liquidity assumptions, the exact Quoter contract implementation (whether it's Uniswap V3 style `quoteExactInputSingle` against current tick, confirmed by grep hits in `x/uexecutor/types/abi.go` and `x/uexecutor/keeper/evm.go`, but full contract source wasn't inspected), or whether any additional TWAP/oracle safeguard exists elsewhere that I did not locate. This uncertainty means the finding should be treated as a plausible analog requiring further confirmation of the Quoter's pricing mechanism and pool liquidity before being considered conclusively exploitable at scale.

### Recommendation
- Short term: do not rely solely on a same-transaction spot quote from the Quoter contract for `minPCOut`; incorporate a TWAP-based reference price or a governance/registry-configured maximum acceptable slippage tied to an external price feed, and/or widen protections against sandwich attacks (e.g., commit-reveal, private mempool routing, or capping deposit-triggered swap size relative to pool depth).
- Long term: route PRC20↔WPC conversions through audited, manipulation-resistant pricing infrastructure (TWAP oracles or a canonical price registry) rather than an instantaneous on-chain quoter, consistent with the original report's long-term recommendation to avoid spot-price-driven economic logic entirely.

### Proof of Concept
Conceptual PoC (not verified end-to-end due to lack of access to deployed pool liquidity and Quoter contract source):
1. Attacker identifies the WPC/PRC20 pool used for a given `tokenConfig.NativeRepresentation.ContractAddress`.
2. Attacker submits a large swap transaction against that pool via a normal EVM transaction, shifting the spot price.
3. Attacker triggers (or already has pending) an inbound deposit for that PRC20 token that will be processed via `ExecuteInboundGasAndPayload` → `gasAndPayloadDepositAutoSwap` → `GetSwapQuote` → `CallPRC20DepositAutoSwap`, executing at the manipulated quote with only 5% slippage protection.
4. Attacker reverses the initial swap, restoring the pool, and nets the difference between fair value and the manipulated `minPCOut`-bounded execution price.

Further verification is needed on: exact Quoter contract logic, WPC/PRC20 pool liquidity depth on live Push Chain deployments, and whether inbound processing timing can be reliably influenced or predicted by an unprivileged attacker.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-379)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L257-272)
```go
}

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
}


```
