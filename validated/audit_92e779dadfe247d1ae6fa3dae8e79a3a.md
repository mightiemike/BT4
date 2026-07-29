## Analysis Result

The Curve “virtual price vs. actual price” bug class does have a concrete analog in this repository: the gas-abstraction auto-swap path in `x/uexecutor` prices deposits using a manipulable spot-price quote and enforces only a fixed 5% slippage bound, with no TWAP or manipulation resistance.

### Title
Hardcoded 5% slippage on spot-price `QuoterV2` quote allows MEV extraction from inbound gas-abstraction auto-swaps - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`)

### Summary
When a `GAS` or `GAS_AND_PAYLOAD` inbound is finalized, the module fetches a swap quote via `GetSwapQuote` (Uniswap V3 `QuoterV2.quoteExactInputSingle`), which reflects the **current spot price** of the on-chain PRC20/WPC pool, then immediately executes `CallPRC20DepositAutoSwap`/`CallPRC20Deposit...AutoSwap` using `minPCOut = quote * 95 / 100` as the only protection [1](#0-0) . Unlike Curve’s `get_virtual_price()` (an invariant-based value resistant to single-block manipulation), a Uniswap V3 `quoteExactInputSingle` result is exactly the value manipulated by ordinary swaps against the same pool [2](#0-1) . The 5% tolerance is hardcoded and not derived from actual market depth/volatility [3](#0-2) .

### Finding Description
The relevant call sequence, executed atomically inside the keeper method that finalizes an inbound (triggered by the quorum-completing `MsgVoteInbound`):
1. `GetDefaultFeeTierForToken` fetches the pool fee tier [4](#0-3) .
2. `GetSwapQuote` calls the Uniswap V3 `QuoterV2` to simulate `quoteExactInputSingle` against the pool’s **current** reserves/tick [2](#0-1) .
3. `minPCOut` is computed as exactly 95% of that quote [5](#0-4) .
4. `CallPRC20DepositAutoSwap` executes the real swap using `minPCOut` as the only floor [6](#0-5) .

Because `MsgVoteInbound` transactions from Universal Validators are ordinary, publicly observable transactions, an unprivileged actor can watch vote counts approach quorum and front-run the quorum-completing vote in the same block: submit a large swap against the PRC20/WPC pool immediately before it to push the spot price down (within the 5% band the deposit-swap tolerates), let the module’s auto-swap execute at the worsened price, then reverse the price afterward (sandwich). This extracts value directly from the user’s bridged gas amount — the same “price computed then consumed later at a possibly divergent rate” pattern the external Curve report describes, except here the “virtual price” substitute (a raw AMM spot quote) offers no flash-loan/manipulation resistance at all, and the 5% band is a fixed, non-adaptive constant rather than a true safety margin.

### Impact Explanation
Every `GAS`/`GAS_AND_PAYLOAD` inbound executed through `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` is systemically exposed to a slippage-extraction attack of up to 5% of the deposited value, funded from the end user’s bridged funds, executable by any unprivileged user who can trade on the target pool — this falls under “corruption of ... gas fee accounting ... or canonical UniversalTx state” and unauthorized value misrouting during universal execution.

### Likelihood Explanation
Moderate-to-high: the attack requires only capital to move the specific PRC20/WPC pool price and to observe pending `MsgVoteInbound` transactions/mempool activity to time the sandwich around the quorum-finalizing vote — no validator, TSS, or admin privilege is needed, and the 5% band is generous enough on illiquid pools to be reliably profitable.

### Recommendation
Replace the raw spot-price `quoteExactInputSingle` value with a manipulation-resistant reference price (e.g., a TWAP oracle or a price bound sourced independently of the same-block pool state), and/or make the slippage tolerance dynamic based on trade size relative to pool liquidity rather than a fixed 5%. Consider also decoupling the moment of quoting from the moment of execution enough that manipulation isn't free to reverse within a single block, or add commit-reveal/anti-sandwich protections around the auto-swap call.

### Proof of Concept
1. Attacker monitors `MsgVoteInbound` submissions for a `GAS`-type inbound approaching 2/3 quorum.
2. Immediately before the quorum-completing vote transaction is included, attacker submits a large swap on the PRC20↔WPC pool that depresses the PRC20 price by close to 5% (within the tolerated band).
3. The quorum-completing transaction triggers `ExecuteInboundGas`, which calls `GetSwapQuote` against the now-manipulated pool and computes `minPCOut` off that skewed quote [7](#0-6) , then executes `CallPRC20DepositAutoSwap` at the worse rate [8](#0-7) .
4. Attacker reverses their initial swap in a following transaction, capturing the value difference extracted from the user's deposit, net of pool fees.

### Citations

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

**File:** x/uexecutor/keeper/evm.go (L470-498)
```go
// GetDefaultFeeTierForToken reads defaultFeeTier[prc20] from UniversalCore.
func (k Keeper) GetDefaultFeeTierForToken(ctx sdk.Context, prc20Address common.Address) (*big.Int, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "defaultFeeTier", prc20Address)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call defaultFeeTier")
	}

	results, err := abi.Methods["defaultFeeTier"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack defaultFeeTier result")
	}

	// go-ethereum unpacks uint24 as *big.Int (non-standard widths always map to *big.Int)
	fee, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for defaultFeeTier: %T", results[0])
	}

	return fee, nil
}
```

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

**File:** x/uexecutor/keeper/evm.go (L540-592)
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L142-153)
```go
							}
						}
					}

					if execErr == nil && amount.Sign() > 0 {
						// --- Step 4 & 5: deposit + autoswap (only when amount > 0)
						prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
						receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}
```
