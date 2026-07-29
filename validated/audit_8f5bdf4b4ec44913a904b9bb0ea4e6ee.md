### Title
Gas-abstraction auto-swap uses manipulatable spot Uniswap V3 quote for slippage protection, enabling sandwich extraction of user/protocol value - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The external report's root cause is a critical financial parameter (stable interest rate) being derived from instantaneous, attacker-manipulable spot state (utilization ratio) instead of a manipulation-resistant source, with the resulting value then consumed immediately in the same execution path to lock in an advantage. The same bug class exists in Push Chain's gas-abstraction flow: `ExecuteInboundGas` fetches a spot AMM quote from a Uniswap V3 `QuoterV2` and derives `minPCOut` slippage protection directly from that same manipulable quote, then immediately executes the swap using it.

### Finding Description
When a user deposits a non-gas asset cross-chain, `ExecuteInboundGas` performs gas abstraction by swapping the deposited PRC20 token for WPC (wrapped native gas token) so gas can be funded for the user's UEA. The flow is:

1. `k.GetSwapQuote(...)` calls `QuoterV2.quoteExactInputSingle` with `commit=false`, which simulates the swap against the *current* pool state (spot price) [1](#0-0) .
2. `minPCOut` is computed as `quote * 95 / 100` — a fixed 5% slippage band applied to that same spot quote [2](#0-1) .
3. `CallPRC20DepositAutoSwap` executes the actual swap on-chain using this self-referential `minPCOut` [3](#0-2) , [4](#0-3) .

Because the "slippage protection" is derived from the same spot price that is used to execute the trade, it does not protect against price manipulation — it only protects against price movement *after* the quote is taken and *before* the swap executes (a very narrow window, but one an attacker fully controls if they can act around the block/EVM call boundary that processes the inbound). An attacker who can push the PRC20/WPC pool price down immediately before this module-driven swap executes (e.g., via a large swap in the underlying Uniswap V3 pool), and reverse it immediately after, causes the module's swap to execute at an artificially unfavorable rate for the depositor while the attacker captures the price-reversion arbitrage. There is no TWAP oracle, external price bound, or manipulation-resistant reference price anywhere in this path — confirmed by the absence of any TWAP/price-bound logic in the codebase (`grep` for TWAP/maxSlippage returns hits only inside this exact swap file, referring to the same 5%-of-spot-quote calculation) [5](#0-4) .

This mirrors the external report's core defect: a protocol-critical financial computation (utilization ratio / swap rate) is derived from instantaneous, attacker-influenceable on-chain state and then immediately consumed to finalize value transfer, with the "protection" band computed relative to the same manipulated value rather than an independent reference.

### Impact Explanation
If exploitable, this allows an unprivileged attacker to extract value from users' deposits during the automatic gas-abstraction swap performed by the module on their behalf — the user ends up with less WPC/PC gas credited than fair market value, while the attacker profits from the price round-trip. This falls under "corruption of PRC20 or native asset accounting" and unauthorized value extraction from protocol/user-controlled funds during a universal execution flow, which is in scope. The refund flow `CallUniversalCoreRefundUnusedGas` has the identical `fee`/`minPCOut` pattern for swap-back of unused gas [6](#0-5) , extending the same weakness to the refund path.

### Likelihood Explanation
Uncertain / not confirmed as practically exploitable from the available scoped code alone. Two key facts I could not verify from the index limit whether they mitigate this:
1. Whether `ExecuteInboundGas` executes deterministically at a point relative to block production that an external attacker can reliably front-run/back-run (i.e., whether the pool-price manipulation and the module's swap can be forced into the same block, or the module call happens at an unpredictable point during ballot finalization across nodes).
2. Whether the actual liquidity/pool used for PRC20↔WPC is deep enough, or whether `UniversalCore`'s Solidity contract itself imposes any additional price-sanity checks not visible in the Go keeper code (the `depositPRC20WithAutoSwap` contract implementation was not available in the index).

Because ballot/inbound processing on Push Chain runs deterministically across all honest validators (not attacker-triggered per-transaction like a normal EVM tx), the attacker's ability to sandwich this specific call depends on timing details of the uexecutor module's block-processing schedule that I was unable to confirm here.

### Recommendation
- Derive `minPCOut` from a manipulation-resistant reference (e.g., a TWAP over the Uniswap V3 pool, or an off-chain/oracle price checked against the spot quote with a maximum-deviation bound) rather than purely from the same spot quote used to execute the trade.
- Consider bounding acceptable slippage relative to a recent time-averaged price rather than the instantaneous `quoteExactInputSingle` result.
- Verify (in the Solidity `UniversalCore`/Handler contract, which was not available in this index) whether any additional protections already exist; if none, add them there since the Go keeper cannot enforce economic safety on its own.

### Proof of Concept
Not independently verified against a running node/testnet fork. Conceptually mirroring the original report's PoC pattern:
1. Attacker monitors pending cross-chain deposit inbounds destined to be processed via `ExecuteInboundGas` for a given PRC20/WPC Uniswap V3 pool.
2. Immediately before the module's `GetSwapQuote` + `CallPRC20DepositAutoSwap` sequence executes, attacker swaps a large amount in the same pool to move the spot price against the deposited asset.
3. The module quotes and executes the deposit-swap at the manipulated price (protected only by 5% of that same manipulated price).
4. Attacker reverses their swap, capturing the price round-trip profit; the depositing user receives less native gas value than fair-market swap would have produced.

A full working exploit would require access to the `UniversalCore`/`depositPRC20WithAutoSwap` Solidity contract source and the exact scheduling of `ExecuteInboundGas` relative to block production, neither of which was available to confirm in this index — recommend a Devin session with full repo/contract access and a local devnet to validate exploitability end-to-end before treating this as confirmed.

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

**File:** x/uexecutor/keeper/evm.go (L595-644)
```go
// CallUniversalCoreRefundUnusedGas calls refundUnusedGas on UniversalCore to return excess gas fee
// to the recipient. withSwap=true swaps the gas token back to PC; withSwap=false deposits PRC20 directly.
func (k Keeper) CallUniversalCoreRefundUnusedGas(
	ctx sdk.Context,
	gasToken common.Address,
	amount *big.Int,
	recipient common.Address,
	withSwap bool,
	fee *big.Int,
	minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	// fee is uint24 in Solidity — pass as *big.Int (go-ethereum ABI packs non-standard widths as *big.Int)
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress,
		handlerAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"refundUnusedGas",
		gasToken,
		amount,
		recipient,
		withSwap,
		fee,
		minPCOut,
	)
}
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
