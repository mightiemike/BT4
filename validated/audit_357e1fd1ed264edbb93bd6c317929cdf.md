## Finding Confirmed

This is a legitimate vulnerability. The code confirms the exact pattern described in the question.

### Title
Sandwichable spot-price swap quote lets attackers extract value from gas-abstraction PRC20→PC auto-swaps - ([File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/evm.go])

### Summary
For every gas-abstraction inbound deposit, `ExecuteInboundGas` (GAS-only) and `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD) fetch a swap quote from Uniswap `QuoterV2.quoteExactInputSingle` and derive `minPCOut` as a flat 5% haircut of that instantaneous quote, then immediately execute `depositPRC20WithAutoSwap` against the same pool. There is no TWAP, external price reference, or protocol-defined minimum, so the "protection" is only ever measured against a spot price that a third party can move.

### Finding Description
`GetSwapQuote` calls the QuoterV2 contract for a live spot price [1](#0-0) , and both call sites compute `minPCOut` purely as `quote * 95 / 100` with no independent floor: `x/uexecutor/keeper/execute_inbound_gas.go` [2](#0-1)  and `x/uexecutor/keeper/execute_inbound_gas_and_payload.go` [3](#0-2) . The actual deposit+swap is performed by `CallPRC20DepositAutoSwap`, which forwards `fee` and `minPCOut` unchanged into `depositPRC20WithAutoSwap` on the `UniversalCore` handler contract [4](#0-3) .

Because the quote and the swap both read the *same* pool state and the slippage bound is derived from that state rather than from a time-weighted or externally-verified fair value, an attacker does not need to interleave a transaction between the quote call and the swap call (which are not separated by any other executable code within block processing). Instead the attacker only needs to move the pool price *before* the inbound is processed (in a prior block, once the inbound/vote is observable/predictable) and reverse it *after* — a standard sandwich pattern. Both the quote and the swap will reflect the manipulated price, so the 5% check is satisfied trivially while the UEA recipient receives far less PC than fair value, and the attacker captures the difference when reversing the price. This directly corrupts native PC balance accounting for the recipient UEA on ordinary user-triggered deposits — no validator, admin, or TSS collusion is required.

### Impact Explanation
Any user's gas-abstraction deposit (bridged PRC20 auto-swapped to native PC for gas) can have its final PC balance for the UEA reduced well below fair value, i.e. material fund loss for depositors, extracted by an unprivileged third party via ordinary DEX trades. This affects the `GAS` flow (`x/uexecutor/keeper/execute_inbound_gas.go`) and the `GAS_AND_PAYLOAD` flow (`x/uexecutor/keeper/execute_inbound_gas_and_payload.go`), both of which are reachable from standard cross-chain deposit submission.

### Likelihood Explanation
Likelihood scales with the liquidity depth of the specific PRC20/WPC pool being used for a given token — low-liquidity pools (likely for newer/bridged assets) are cheaply manipulable within a single block window. The attacker needs no special access: only the ability to observe/predict when a target inbound will be finalized and to submit ordinary swap transactions against the same pool before and after.

### Recommendation
Do not derive `minPCOut` solely from the same-block spot quote. Options: (1) use a TWAP-based quote (e.g., Uniswap V3 `observe`) instead of `quoteExactInputSingle`, (2) enforce a protocol-configured maximum-deviation check between the spot quote and a longer-window reference price before accepting the swap, or (3) let the caller (or governance-configured parameter) supply an independent floor price not derived purely from the manipulable pool being traded against.

### Proof of Concept
Unit-test `gasAndPayloadDepositAutoSwap` (or `ExecuteInboundGas`) with a mocked/manipulated `QuoterV2` and pool state such that:
1. Attacker trade skews the pool price up before `GetSwapQuote` is invoked.
2. `GetSwapQuote` returns the skewed quote; `minPCOut = quote*95/100` is computed from it.
3. `CallPRC20DepositAutoSwap` executes and satisfies `minPCOut`, but the amount of PC actually credited to the UEA is far below the pre-manipulation fair-value quote.
4. Assert that received PC has no protocol-defined floor independent of the single pool's manipulable spot price — confirming the slippage check provides no real protection against sandwiching. [5](#0-4)  and [6](#0-5)  demonstrate that quote and slippage bound are computed from the same untrusted pool state used for execution, with no TWAP or external floor.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-378)
```go
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
