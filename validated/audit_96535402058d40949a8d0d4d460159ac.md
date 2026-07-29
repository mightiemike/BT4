## Finding

### Title
Auto-swap slippage protection derived from an unprotected Uniswap V3 spot quote enables flashloan/sandwich-style value extraction from the PRC20↔WPC gas-abstraction pool - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
When an inbound deposit triggers Push Chain's "gas abstraction" flow, the `uexecutor` module auto-swaps the user's deposited PRC20 token into WPC (wrapped native gas token) by calling `UniversalCore.depositPRC20WithAutoSwap`. The minimum acceptable output (`minPCOut`) for that swap is derived entirely from a single, un-TWAP'd, instantaneous Uniswap V3 quote (`QuoterV2.quoteExactInputSingle`) fetched immediately before the swap executes, with a flat 5% slippage tolerance applied on top of that same quote. Because the bound is self-referential to the manipulable spot price rather than an independent, time-weighted reference price, this is the same bug class as the reported ParaSpace fallback oracle finding: a critical accounting decision is priced off a spot AMM reserve ratio that any unprivileged actor can move with a flashloan/large swap immediately before the module-driven swap executes.

### Finding Description
`GetSwapQuote` calls the Uniswap V3 `QuoterV2.quoteExactInputSingle` as a pure view call against the live pool reserves: [1](#0-0) 

The keeper then computes `minPCOut` as a flat 95% of that very quote and passes it straight into the real swap call, with no independent TWAP, no external price reference, and no check that the quote itself reflects an undisturbed pool state: [2](#0-1) [3](#0-2) 

This same pattern is used on both the `ExecuteInboundGas` (gas-only) and `ExecuteInboundGasAndPayload` (gas+payload / isCEA) paths, both of which are reached deterministically once an inbound vote crosses quorum and the module executes `depositPRC20WithAutoSwap` on `UniversalCore` via `CallPRC20DepositAutoSwap`: [4](#0-3) 

Because `minPCOut` is computed from, and immediately consumed by, the same manipulable pool state, the 5% slippage guard provides no protection against a pool whose reserves were skewed by an attacker's swap in the same or an immediately preceding block. An unprivileged actor can:
1. Push the PRC20/WPC pool price in their favor with a large or flash-loaned swap.
2. Trigger (or wait for/predict) the deterministic auto-swap that fires when their own — or anyone's — inbound deposit finalizes.
3. The quote and the real swap both execute against the skewed reserves, so the module willingly accepts an unfavorable conversion rate, paying out WPC (or accepting less WPC than fair value) at the manipulated price, extracting value from the protocol-provided PRC20↔WPC liquidity.
4. Reverse the initial swap, restoring the pool and pocketing the difference.

This mirrors the reported bug class exactly: reliance on an AMM spot price with no TWAP/oracle circuit-breaker for a value-bearing accounting decision.

### Impact Explanation
This affects the module's own gas-abstraction liquidity (`UniversalCore`'s Uniswap V3 PRC20/WPC pool), which is protocol-controlled and used to fund every gasless/auto-swap deposit. An attacker who can move that pool's price (via flashloan-style capital, if any lending/flashloan primitive is deployed on Push Chain EVM, or simply large capital plus MEV-style transaction ordering) can repeatedly extract value from this pool on each inbound deposit that routes through `depositPRC20WithAutoSwap`, corrupting PRC20/native asset accounting and draining protocol-controlled funds — squarely within the "corruption of PRC20 or native asset accounting" and "stealing ... permanent loss ... of protocol-controlled funds" impact categories.

### Likelihood Explanation
Likelihood depends on (a) the depth/liquidity of the on-chain PRC20/WPC Uniswap V3 pool and (b) the ability of an unprivileged actor to manipulate it and to have a deposit's auto-swap execute against the manipulated state. Since inbound execution timing is driven by validator quorum rather than the attacker's own transaction, this is not a fully atomic single-transaction flashloan attack like the original report, but remains exploitable via thin liquidity + repeated/likely timing windows (the attacker can also be the depositor, guaranteeing the swap fires right after they've skewed the pool). This makes it a realistic, not merely theoretical, risk, though somewhat less deterministic than a same-transaction exploit.

### Recommendation
Do not derive `minPCOut` solely from an instantaneous `quoteExactInputSingle` call. Use a TWAP-based reference price (e.g., Uniswap V3 pool `observe`/oracle cardinality) or an independent price source (such as the validator-voted `ChainMeta`/gas price mechanism already used elsewhere in this module) to bound acceptable swap output, and/or enforce a maximum single-block price deviation check before allowing the auto-swap to proceed, reverting (rather than swapping) when the quote is inconsistent with the reference price.

### Proof of Concept
1. Attacker (or colluding party) identifies the Uniswap V3 pool between a given PRC20 and WPC used by `UniversalCore` (address returned via `GetUniversalCoreWPCAddress`/`GetUniversalCoreQuoterAddress`).
2. Attacker executes a large swap (funded via flashloan if available, or large capital) against that pool to skew its price in the direction favorable to their upcoming deposit.
3. Attacker submits an inbound deposit of the PRC20 token; once validator quorum finalizes the inbound, `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` calls `GetSwapQuote` then `CallPRC20DepositAutoSwap`, both against the still-skewed pool (`x/uexecutor/keeper/evm.go:500-593`).
4. The module-driven swap executes at the manipulated rate, transferring more WPC to the attacker's UEA (or accepting a below-fair-value amount into the pool) than the pool's true reserves justify.
5. Attacker reverses their initial swap, restoring the pool price and realizing the extracted value, at the expense of the protocol-provided PRC20/WPC liquidity.

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
