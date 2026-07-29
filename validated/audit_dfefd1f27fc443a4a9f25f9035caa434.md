### Title
Unvalidated spot-price AMM quote used as pricing oracle for gas-abstraction swaps enables sandwich/manipulation of user deposits - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
The external report's bug class is "an oracle's price data is consumed without validating freshness/completeness, letting a stale or malformed value drive protocol accounting." Push Chain's `x/uexecutor` module has its own oracle-like data source, the on-chain Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote, that is fetched and consumed as the sole price reference for GAS / GAS_AND_PAYLOAD inbound swaps and for gas-fee refunds — with no freshness, TWAP, or deviation validation. Unlike the `ChainMeta`/gas-price oracle in `x/uexecutor/keeper/chain_meta.go` (which the team hardened with staleness windows, monotonic height checks, and a bootstrap quorum), the QuoterV2-based swap price has no analogous protection: the "slippage" bound is derived from the same manipulable spot price it's supposed to protect against.

### Finding Description
`Keeper.GetSwapQuote` in [1](#0-0)  calls `QuoterV2.quoteExactInputSingle` with a live (non-committed) EVM call to obtain the current spot price of the underlying Uniswap V3 pool. This quote is then used to compute the acceptance bound for the actual swap: [2](#0-1) 

```go
quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
...
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

The identical pattern is used for `GAS_AND_PAYLOAD` in `gasAndPayloadDepositAutoSwap` [3](#0-2)  and for gas-fee refunds via `CallUniversalCoreRefundUnusedGas` (feature added per the upgrade log at [4](#0-3) ).

The 5% tolerance (`minPCOut = quote * 95/100`) only defends against the module's own swap-router execution slippage between the `quote` call and the actual swap call within the same keeper invocation. It does **not** defend against the underlying pool price itself being artificially moved before this code runs. There is no TWAP observation, no comparison against `ChainMeta`'s cross-validator-agreed price, and no sanity bound — this is exactly the class of missing validation flagged in the external Chainlink report ("validate that oracle data is fresh / from a legitimate source before trusting it for a financial computation"), except here the untrusted external input is an on-chain AMM spot price rather than a Chainlink round.

Critically, this swap executes **synchronously inside `MsgVoteInbound`**, in the very transaction of whichever validator's vote finalizes the ballot: `VoteInbound` → `ExecuteInbound` → `ExecuteInboundGas` [5](#0-4) . Because block/transaction ordering within a block is attacker-influenceable (an unprivileged user can submit ordinary EVM transactions against the Uniswap V3 pool used for `prc20`↔`WPC` pricing), an attacker can:
1. Observe a pending cross-chain GAS/GAS_AND_PAYLOAD inbound (visible via pending-inbound queries / mempool of validator votes).
2. Submit a large swap against the same Uniswap V3 pool just before the finalizing `MsgVoteInbound` transaction lands, moving the spot price against the victim.
3. Let `GetSwapQuote` read the manipulated price and derive `minPCOut` from it — the 5% tolerance offers no protection since it is computed from the manipulated number itself.
4. Optionally reverse the manipulation afterward (classic sandwich), extracting value from the user's deposit at the module's expense (fewer PC out are minted to the user's UEA than the honest market rate would produce), or draining pool liquidity/protocol-held funds depending on pool depth relative to deposit size.

### Impact Explanation
This corrupts PRC20/native asset accounting during universal execution: the amount of PC (native gas token) minted/deposited to the user's UEA from `depositPRC20WithAutoSwap` is determined by a manipulable, unvalidated price, directly causing user fund loss or windfall gain at protocol expense. This matches the in-scope impact category "corruption of PRC20 or native asset accounting ... token mapping ... canonical UniversalTx state" and is reachable purely through "ordinary user deposits ... or default transaction submission paths alone" — no validator, admin, or TSS compromise is required, only unprivileged EVM transactions on Push Chain.

### Likelihood Explanation
Likelihood depends on the liquidity depth of the specific Uniswap V3 pool configured for a given token pair and on the attacker's ability to time transactions around ballot finalization, which is plausible since inbound execution happens deterministically and synchronously inside a specific `MsgVoteInbound` tx once quorum is reached (a public, observable event). For thin pools this is straightforward; for deep pools it requires more capital but remains feasible (flash-loan-funded manipulation is a standard technique). This is a design gap rather than a rare edge case, so it is realistically exploitable rather than purely theoretical.

### Recommendation
- Do not derive the slippage bound solely from the same spot quote used for the swap; introduce a TWAP-based reference price (e.g., using `Oracle.observe`/geomean over a window) or cross-check against the validator-attested `ChainMeta`/registry-configured reference price before accepting a deposit swap.
- Add a maximum-deviation check between the QuoterV2 spot quote and a longer-window TWAP; revert/queue the deposit as raw PRC20 (no swap) if deviation exceeds a safe threshold, consistent with how `GAS_AND_PAYLOAD`/`GAS` routes already have a no-swap fallback path.
- Consider bounding `sqrtPriceLimitX96` in `AbiQuoteExactInputSingleParams` (currently `big.NewInt(0)`, i.e., unbounded) to cap the worst-case executed price rather than only the amount-out.
- Apply the same treatment consistently to `CallUniversalCoreRefundUnusedGas`'s swap-back path, which uses the identical unguarded pattern.

### Proof of Concept
1. Identify a pending `GAS` or `GAS_AND_PAYLOAD` cross-chain inbound destined for a token whose PRC20↔WPC Uniswap V3 pool has moderate liquidity.
2. Before/around the block in which the finalizing `MsgVoteInbound` transaction is expected to land, submit a large swap on that Uniswap V3 pool to move the spot price unfavorably for the pending deposit's swap direction.
3. When `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` runs, `GetSwapQuote` returns the manipulated price; `minPCOut` is computed as 95% of that manipulated value, so `CallPRC20DepositAutoSwap` executes at the bad price without reverting.
4. Reverse the initial swap in a follow-up transaction to restore the pool and capture the price difference, at the expense of the value the victim's UEA should have received.

Note: I could not fully verify the exact block-level scheduling/ordering guarantees for when `MsgVoteInbound` transactions are included relative to attacker transactions (e.g., whether Push Chain's mempool/consensus ordering rules make sandwiching trivial or merely feasible); this would benefit from further live-network or simulation verification via a Devin session with full repository and node access.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
```

**File:** app/upgrades/chain-meta/upgrade.go (L77-84)
```go
		// ── Feature 6 ───────────────────────────────────────────────────────────
		// On a successful outbound observation, if gas_fee_used < gas_fee the
		// excess is refunded to the sender (or fund_recipient) via
		// UniversalCore.refundUnusedGas.  A swap (gasToken → PC native) is
		// attempted first; on failure the raw PRC20 is deposited directly.
		// The result is persisted in OutboundTx.pc_refund_execution.
		// No state migration required.
		logger.Info("Feature: excess gas fee refund executed on successful outbound vote finalisation")
```

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
