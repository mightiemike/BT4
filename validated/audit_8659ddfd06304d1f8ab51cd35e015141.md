### Title
Gas-Abstraction Auto-Swap Uses a Manipulable Spot-Price Quote as Its Own Slippage Reference, Enabling Value Extraction from Inbound Depositors - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go)

### Summary
The bug-class in the external report is a share/price-inflation attack where a single actor manipulates a value that the protocol itself uses moments later as the "fair" reference price, and legitimate users then transact against the poisoned reference with no external safety check. Push Chain's gas-abstraction inbound flow reproduces the same structural flaw: it fetches a live spot-price quote from a Uniswap V3 pool and then immediately executes the swap using a slippage bound derived from that same just-fetched quote, with no TWAP or external price reference.

### Finding Description
When an `Inbound` of type gas is finalized by validator quorum, `ExecuteInboundGas` [1](#0-0)  drives the deposit-and-swap sequence:

1. `GetDefaultFeeTierForToken` and `GetSwapQuote` are called to read the *current* Uniswap V3 `QuoterV2.quoteExactInputSingle` output for the `prc20 → wpc` pair [2](#0-1) .
2. The keeper then computes `minPCOut` as exactly `quote * 95 / 100` — a 5% band around the value just read from the pool — and immediately calls `CallPRC20DepositAutoSwap` with that bound [3](#0-2) .

There is no TWAP, no external oracle cross-check, and no reference to a price recorded before the attacker could have acted (the `ChainMeta`/gas-price oracle used elsewhere in this module explicitly guards against single-actor manipulation via a `chainMetaMinVotesForFirstWrite` bootstrap quorum [4](#0-3) , but no equivalent protection exists for the swap-quote path). Because the quote and the slippage bound are derived from the identical, attacker-manipulable spot state of the pool at execution time, the 5% tolerance only protects against noise in an already-poisoned price — it does not protect against the price having been deliberately pushed away from fair value immediately beforehand.

An unprivileged attacker who can place a transaction ordered immediately before the block/transaction that finalizes the inbound vote (inbound observations and votes are publicly visible in the mempool before quorum is reached) can:
- Swap heavily against the `prc20/wpc` pool to push its spot price away from fair value.
- Let `ExecuteInboundGas`'s deposit-and-swap execute against that skewed price (the 5% band moves with the skewed price, so it does not block the swap).
- Reverse their initial swap afterward, capturing the value that was extracted from the victim's gas-abstraction deposit.

### Impact Explanation
This corrupts PRC20/native asset accounting for the swap step of the gas-abstraction flow: the recipient's UEA receives materially less `WPC`/native gas token than the fair-value swap would have produced, and the difference is captured by the attacker. This falls within the explicitly allowed impact categories ("corruption of PRC20 or native asset accounting, gas fee accounting... token mapping... must not misroute value") since it is reachable purely through ordinary user/attacker transaction submission with honest validators and honest nodes — no privileged or colluding-validator assumption is required.

### Likelihood Explanation
Exploitability depends on the depth/liquidity of the specific `prc20/wpc` Uniswap V3 pool used for a given asset; thinly-liquidated pools (newly listed tokens, low-volume chains) are the most exposed, and Push Chain's design explicitly anticipates many external assets being onboarded via `uregistry` token configs, so newly-added or low-volume pairs are a realistic scenario. The attack requires only an ordinary EVM transaction that can be ordered adjacent to the (publicly observable) inbound-finalizing transaction — no validator or relayer collusion is needed, matching the "unprivileged external attacker" threat model.

### Recommendation
Do not derive the slippage floor from a spot quote taken in the same execution as the swap. Use a time-weighted average price (TWAP) from the pool (or an external, already-validated `ChainMeta`-style multi-vote price feed) as the fair-value reference, and only apply the tolerance band on top of that reference. Alternatively, require the swap's minimum output to be bounded by a value that cannot move materially within a single block (e.g., cached price with a staleness/deviation check against the live quote), rejecting execution instead of silently accepting a skewed price.

### Proof of Concept
1. Attacker identifies a gas-abstraction `Inbound` (`TxType_GAS` / `GAS_AND_PAYLOAD`) in flight for a token with a thin `prc20/wpc` Uniswap V3 pool.
2. Before the inbound reaches validator quorum and `ExecuteInboundGas` executes, attacker submits a large swap against the pool to skew its spot price (e.g., buying `wpc` to raise its price relative to `prc20`).
3. `GetSwapQuote` (`x/uexecutor/keeper/evm.go:502-538`) returns the skewed price; `minPCOut = quote*95/100` is computed from it (`x/uexecutor/keeper/execute_inbound_gas.go:142-153`); `CallPRC20DepositAutoSwap` executes against the still-skewed pool, so the victim's deposit swaps at the bad rate.
4. Attacker reverses their initial swap, extracting the value difference from the victim's deposit-driven swap.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L14-24)
```go
func (k Keeper) ExecuteInboundGas(ctx context.Context, inbound types.Inbound) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	ueModuleAccAddress, ueModuleAddressStr := k.GetUeModuleAddress(ctx)
	universalTxKey := types.GetInboundUniversalTxKey(inbound)

	k.Logger().Info("execute inbound gas: gas abstraction swap",
		"utx_key", universalTxKey,
		"source_chain", inbound.SourceChain,
		"amount", inbound.Amount,
		"sender", inbound.Sender,
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-153)
```go
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

**File:** x/uexecutor/keeper/chain_meta.go (L46-61)
```go
// VoteChainMeta processes a universal validator's vote on chain metadata (gas price + chain height).
//
// Rules:
//  1. Each vote is stamped with the current block time (storedAt) when it is recorded
//     and either inserted (new validator) or updated in place (existing validator).
//  2. The oracle is bootstrapped on the first EVM write only after at least
//     chainMetaMinVotesForFirstWrite fresh votes have accumulated. Earlier
//     votes are stored but do not yet drive an on-chain update — this prevents
//     a single validator from defining the oracle's initial values.
//  3. Once bootstrapped (LastAppliedChainHeight > 0), votes whose blockNumber
//     is not strictly greater than entry.LastAppliedChainHeight are rejected —
//     the validator must re-vote with a newer block height.
//  4. When computing medians, only votes whose storedAt is within the last
//     chainMetaVoteStalenessSeconds seconds are considered.
//  5. Price median and chain-height median are computed independently (upper median = len/2).
//  6. After a successful EVM call, LastAppliedChainHeight is updated.
```
