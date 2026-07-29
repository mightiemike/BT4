This confirms `GetSwapQuote` reads the live spot price from Uniswap V3's `QuoterV2.quoteExactInputSingle` at the moment of execution [1](#0-0) , and the resulting `minPCOut` is derived from that same-block spot price with a flat 5% slippage tolerance before the actual swap executes [2](#0-1) [3](#0-2) . This is a legitimate analog to the "oracle price" bug class (spot-price-derived value consumed without manipulation-resistance), but the actual exploitability depends on Push Chain's block-production/mempool model (whether an attacker can reliably insert a same-block sandwich transaction before/after the module-originated swap), which I can't fully confirm from static code alone — that requires understanding CometBFT block proposer/mempool ordering specifics for this chain, which isn't something I can verify with confidence from the indexed code.

### Title
Unprotected spot-price quoting in `UniversalCore` auto-swap paths enables sandwich extraction of protocol/user funds - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`GetSwapQuote` fetches the *current, same-block* spot price from the Uniswap V3 `QuoterV2` contract deployed on Push Chain's own EVM, and both the inbound auto-swap path (`CallPRC20DepositAutoSwap`) and the outbound gas-refund path (`applyGasRefund` → `CallUniversalCoreRefundUnusedGas`) compute `minPCOut` as merely `quote * 95 / 100` — a flat 5% band around that same spot quote — rather than against any manipulation-resistant reference (e.g. a TWAP, an external oracle, or a governance-set floor price) [4](#0-3) [3](#0-2) .

### Finding Description
Both auto-swap call sites follow the identical pattern: fetch a fee tier, call `GetSwapQuote` for the live pool price, derive `minPCOut = quote * 95/100`, then immediately execute the deposit/refund-with-swap in the same keeper call within the same Cosmos transaction [5](#0-4) [3](#0-2) . Because the "protection" slippage bound is computed from the very same spot price that is used for the swap, it does not defend against price manipulation that occurs in the same block window (e.g., a large swap against the WPC/PRC20 pool placed immediately before the module's swap-triggering transaction is included) — the quote and the swap both see the manipulated price, so the 5% band is satisfied trivially even though the pool has been distorted. This mirrors the Fei report's core complaint: a price-reporting mechanism is consulted without any check on its freshness/manipulation-resistance ("isOutdated" analog), and downstream code (the swap execution) blindly trusts the number.

### Impact Explanation
If exploitable, an attacker could extract value from the protocol-controlled `UniversalCore` swap path (inbound PRC20→PC auto-swap, or outbound excess-gas refund swap) by distorting the pool price immediately around the block in which the module-originated swap executes, profiting at the expense of the protocol's PC/PRC20 reserves or the end-user's expected refund/deposit amount. This falls under "corruption of PRC20 or native asset accounting" and "draining ... of protocol-controlled funds" in the allowed impact list.

### Likelihood Explanation
Likelihood depends heavily on block-production and transaction-ordering guarantees of the Push Chain consensus/mempool that I could not fully verify from the code alone — specifically whether an unprivileged party can reliably land a manipulation transaction immediately adjacent to the validator-driven `MsgVoteInbound`/`MsgVoteOutbound` transaction that triggers the swap. If Push Chain's proposer/mempool ordering allows ordinary users to influence adjacency (as in most permissionless mempools), likelihood is non-trivial; if the module's swap always executes deterministically outside of any user-influenced ordering window (e.g., BeginBlock/EndBlock with no attacker-observable interleaving), likelihood drops significantly.

### Recommendation
Replace the same-block spot quote with a manipulation-resistant reference: use a time-weighted average price (TWAP) from the Uniswap V3 pool's observation buffer, cap the acceptable deviation between spot and TWAP before allowing the swap, or fall back to the ChainMeta-style multi-validator-attested price/oracle instead of an instantaneous `QuoterV2` call. Additionally, document explicitly (as `DERIVED_TRANSACTIONS.md` already documents nonce/gasless semantics) what invariant `minPCOut` is meant to protect, so future call sites don't reintroduce the same-price-source flaw.

### Proof of Concept
Not independently verified against a running node — requires confirming Push Chain's actual transaction-ordering/mempool model to demonstrate that an attacker transaction can land adjacent to the module-triggering vote transaction within the same block. Conceptually: (1) attacker swaps a large amount into/out of the WPC/PRC20 pool to skew the spot price, (2) the validator's `MsgVoteInbound`/`MsgVoteOutbound` transaction is processed while the pool is skewed, causing `GetSwapQuote` to return the skewed price and `minPCOut` to be computed from it, (3) the module's swap executes at the skewed price, transferring value to the attacker who then reverses their initial swap.

### Citations

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
