### Title
Attacker-manipulable spot AMM price used as gas-swap oracle, allowing value extraction from the UniversalCore swap pool during `ExecuteInboundGas` - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The gas-abstraction auto-swap path (`ExecuteInboundGas` → `GetSwapQuote` → `CallPRC20DepositAutoSwap`) prices the PRC20→WPC conversion using a single spot quote from Uniswap V3's `QuoterV2.quoteExactInputSingle`, then derives its own slippage floor (`minPCOut`) from that same manipulable quote. This is the same class of bug as the reported "exchange rate manipulated via markPrice" finding: a value that should reflect a fair, time-resistant price is instead read directly off state an unprivileged actor can move, and that same manipulated value is then used to authorize a fund transfer to an address the attacker controls (their own UEA).

### Finding Description
When an inbound deposit needs gas abstraction, `ExecuteInboundGas` [1](#0-0)  does the following, all within one atomic execution:
1. Calls `GetSwapQuote`, which invokes `QuoterV2.quoteExactInputSingle` with `commit=false` against the live Uniswap V3 pool state to estimate how much WPC a given PRC20 amount is worth right now [2](#0-1) .
2. Computes `minPCOut = quote * 95 / 100` — i.e., derives its own slippage protection directly from the just-fetched, unprotected spot quote.
3. Calls `CallPRC20DepositAutoSwap`, which performs the actual on-chain swap with that self-referential `minPCOut` as the floor [3](#0-2) .

`quoteExactInputSingle` reflects the pool's instantaneous spot price, not a time-weighted average. Nothing in this flow (no TWAP window, no external reference price, no circuit breaker) prevents the pool's spot price from having been pushed away from fair value by a prior swap. Since `minPCOut` is computed from the same skewed quote rather than an independent fair-value reference, the swap will always "pass" its own slippage check even when the pool is heavily skewed — the check only guards against price movement *between* the quote and the swap call (which is atomic here and thus not exploitable), not against the *quote itself* being wrong.

An unprivileged actor who can execute ordinary swaps against the UniversalCore's PRC20/WPC pool (a normal, permissionless DEX pool) can:
1. Execute a large swap to skew the pool price so that PRC20 appears artificially expensive in WPC terms.
2. Submit a (even small) cross-chain deposit that is CEA/gas-abstracted, whose recipient is their own UEA (`types.UniversalAccountId` derived from `inbound.Sender`, resolved via `CallFactoryToGetUEAAddressForOrigin`) [4](#0-3) .
3. When validator votes finalize this inbound, `ExecuteInboundGas` reads the skewed quote and swaps at the inflated rate, transferring an inflated amount of WPC out of the shared UniversalCore pool into the attacker's own UEA.
4. Attacker reverses the initial skewing swap, recovering most of the capital used to move the price while pocketing the excess WPC extracted from the pool — a variant of the exact "manipulate price used in an internal exchange-rate/value calculation" pattern described in the source finding, just instantiated against an AMM spot quote instead of a perp funding-driven mark price.

### Impact Explanation
This directly maps to the "PRC20 or native asset accounting" and "gas token selection" corruption categories in the Allowed Impact Gate: the UniversalCore contract's swap pool (protocol-controlled liquidity used for every user's gas-abstraction swap) can be drained of WPC value by a purely unprivileged actor manipulating a permissionless AMM pool's spot price and then triggering their own deposit to swap against it, receiving more value than deposited. This is unauthorized extraction of protocol/pool funds through a user-reachable execution path (deposit → `ExecuteInboundGas`), not requiring any privileged role, malicious validator, or external chain compromise.

### Likelihood Explanation
Like the source finding, this requires capital and is bounded by pool depth/fees, and the gain must exceed the cost of moving the price and reversing it (a function of pool liquidity, Uniswap V3 fee tier, and how "thin" the PRC20/WPC pool is). For thinly liquidated or newly listed PRC20/WPC pairs this is the classic single-block/short-window spot-price AMM manipulation, well documented as low-to-medium likelihood but a real, capital-gated attack path — matching the "valid but unlikely, hence Medium" judgment reached in the original report.

### Recommendation
Do not derive the swap's slippage floor from the same spot quote used to execute the swap. Instead:
- Use a TWAP (time-weighted average price) from the Uniswap V3 pool (or an external, validator-attested price such as the `ChainMeta`/gas-price oracle) as the reference price for computing `minPCOut`, rather than `QuoterV2.quoteExactInputSingle`'s instantaneous quote.
- Add a sanity bound comparing the spot quote against a longer-window reference price and reject/skip the auto-swap (falling back to a revert/rescue path) if they diverge beyond a safe threshold.
- Consider capping the maximum single-swap size relative to pool liquidity for the gas-abstraction auto-swap.

### Proof of Concept
Conceptual reproduction (would need to be adapted to the repo's integration test harness such as `test/integration/uexecutor/inbound_cea_payload_test.go`):
1. Deploy/seed a shallow Uniswap V3 PRC20/WPC pool via UniversalCore.
2. As an unprivileged actor, execute a large `exactInputSingle` swap PRC20→WPC (or WPC→PRC20) to skew the pool's spot price.
3. Submit an inbound CEA deposit (small PRC20 amount, gasless/gas-abstracted) whose sender resolves to the attacker's own UEA.
4. Drive the inbound through `ExecVoteInbound` to quorum so `ExecuteInboundGas` executes `GetSwapQuote` + `CallPRC20DepositAutoSwap` against the now-skewed pool.
5. Observe that the WPC amount deposited into the attacker's UEA is disproportionate to the PRC20 deposited, reflecting the manipulated spot price rather than a fair-value reference.
6. Reverse the initial skewing swap and compare net PRC20/WPC balance to confirm value extraction from the pool. [5](#0-4)

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L60-68)
```go
			} else {
				universalAccountId := types.UniversalAccountId{
					ChainNamespace: chainNamespace,
					ChainId:        chainId,
					Owner:          inbound.Sender,
				}
				factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

				ueaAddr, isDeployed, fErr := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, ueModuleAccAddress, factoryAddress, &universalAccountId)
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L102-153)
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
