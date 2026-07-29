Based on my research, I found a plausible native analog in Push Chain's `x/uexecutor` gas-swap path, where a fixed 5% slippage tolerance on protocol-executed swaps mirrors the DODO/MagicLP sandwich-vulnerability pattern (price-affecting operation executed with a wide, static, and predictable margin that an MEV actor can extract).

### Title
Fixed 5% slippage tolerance on protocol-driven `depositPRC20WithAutoSwap` allows MEV sandwich extraction of user deposit value - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
When an inbound deposit requires gas-token conversion, `ExecuteInboundGas` fetches a swap quote from the on-chain Uniswap V3 `QuoterV2` and then immediately executes `depositPRC20WithAutoSwap` against a hardcoded `minPCOut` computed as `quote * 95 / 100` — a fixed 5% slippage allowance [1](#0-0) . This is analogous to the reported DODO/MagicLP issue: a protocol-controlled operation that moves price/value is executed with a static, wide, and predictable tolerance, which an unprivileged actor can exploit via front-running/back-running (sandwiching) the pool used for the swap.

### Finding Description
`GetSwapQuote` performs a simulated (`commit=false`) call to `QuoterV2.quoteExactInputSingle` to price the `prc20 -> wpc` conversion [2](#0-1) . Immediately after, the keeper computes `minPCOut` using a fixed 95% ratio and calls `CallPRC20DepositAutoSwap`, which performs the real swap via `DerivedEVMCall` to `depositPRC20WithAutoSwap` on `UniversalCore` [3](#0-2) . Because this whole sequence is deterministic, triggered purely by an ordinary/unprivileged user's inbound deposit, and the pool used (Uniswap V3 `prc20/wpc` pair on Push Chain's EVM) is a public AMM, any actor observing the pending inbound vote reaching quorum can manipulate the pool price immediately before the module's derived swap executes (and reverse it afterward), extracting value up to the full 5% slippage window at the expense of the depositing user / protocol-held funds. This is the same underlying bug class as `MagicLP.setParameters()`: a fixed, non-adaptive tolerance around a price-sensitive operation that creates a guaranteed profitable sandwich window for anyone who can order transactions around it.

### Impact Explanation
Each inbound deposit that goes through the auto-swap path can lose up to ~5% of its gas-token value to a sandwiching actor, which constitutes "corruption of ... gas fee accounting, refund accounting ... or unauthorized state transitions in universal execution flows" and a form of fund loss for the depositing user, within scope of the allowed-impact gate (unprivileged attacker draining/loss of user-controlled funds through the universal execution flow).

### Likelihood Explanation
Likelihood is moderate-to-high on any chain where the `prc20/wpc` Uniswap V3 pool has limited liquidity relative to deposit sizes, since the swap trigger (`MsgVoteInbound` finalization) and its parameters are observable on-chain before execution, and Cosmos SDK block proposers/searchers can order transactions within the same block to sandwich the derived EVM call.

### Recommendation
Replace the fixed 95% slippage constant with a dynamically computed, tighter bound (e.g., TWAP-based pricing, or a much smaller configurable tolerance), and/or execute the quote and swap atomically using commit-reveal or same-transaction guarantees that remove attacker ordering opportunities. Consider capping slippage loss per deposit and/or routing through a private/internal execution path not exposed to front-running.

### Proof of Concept
1. Monitor `x/uexecutor` for `MsgVoteInbound` transactions nearing the voting threshold for a gasless deposit requiring auto-swap.
2. Once the ballot is about to finalize (last required vote observed in mempool), submit a large swap transaction against the same `prc20/wpc` Uniswap V3 pool to move the price against the pending deposit swap.
3. Allow the module's `depositPRC20WithAutoSwap` call to execute at the worse price still within the 5% `minPCOut` tolerance [1](#0-0) .
4. Submit a reverse swap transaction immediately after to restore the pool price, capturing the price difference as profit, at the expense of the value delivered to the depositor/protocol.

Note: This finding relies on inference about block/transaction ordering behavior in the Cosmos SDK EVM integration; verifying the precise atomicity guarantees between quote-fetch and swap-execution (whether both occur strictly within the same isolated execution context immune to reordering) would require deeper review of the ABCI/block-processing pipeline, which the current index does not fully expose. If this repo's execution model guarantees strict atomicity with no attacker-observable ordering window, this finding would not be exploitable and should be discounted accordingly.

### Citations

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
