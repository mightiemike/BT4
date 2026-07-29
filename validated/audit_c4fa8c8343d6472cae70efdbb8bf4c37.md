This confirms the mechanism: `ExecuteInboundGas` computes `minPCOut` purely from a single spot-price call to `QuoterV2.quoteExactInputSingle` [1](#0-0)  taken in the very same execution as the deposit-and-swap call [2](#0-1) , with only a flat 5% slippage buffer and no TWAP or minimum-liquidity check [3](#0-2) . The same pattern repeats for `GAS_AND_PAYLOAD` in `gasAndPayloadDepositAutoSwap` [4](#0-3) .

### Title
Spot-price-derived `minPCOut` slippage bound in GAS/GAS_AND_PAYLOAD autoswap enables sandwich-style value extraction from user deposits - ([File: x/uexecutor/keeper/execute_inbound_gas.go])

### Summary
The external report describes a bank-module analog where the absence of a minimum-liquidity floor lets an attacker manipulate an exchange rate (bToken value) so that a legitimate deposit mints zero/near-zero shares, causing user loss. The scoped analog in Push Chain is the GAS-token autoswap path in `x/uexecutor`: `ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` derive the swap's minimum-output protection (`minPCOut`) exclusively from a same-call spot quote via Uniswap V3's `QuoterV2.quoteExactInputSingle`, with only a static 5% slippage cushion and no independent minimum-liquidity/TWAP safeguard [5](#0-4) .

### Finding Description
When a user bridges a gas-paying asset in (`TxType_GAS` / `TxType_GAS_AND_PAYLOAD`), once validator quorum is reached, `ExecuteInboundGas` fetches the current spot quote from the configured Uniswap V3 pool (`GetSwapQuote`) and sets `minPCOut = quote * 95 / 100`, then immediately calls `CallPRC20DepositAutoSwap`, which drives the on-chain `depositPRC20WithAutoSwap` swap [6](#0-5) [2](#0-1) . Both the reference price and the trade execute back-to-back with no external price validation, no oracle cross-check, and no floor on the pool's available liquidity. An unprivileged external actor who controls funds in the underlying Uniswap V3 pool can submit a large swap immediately before the inbound-finalizing transaction lands in a block (front-running the validator quorum tx that triggers `ExecuteInboundGas`), depressing the spot price so that both the fetched `quote` and the resulting `minPCOut` are artificially low. The protocol then executes the user's real GAS deposit swap at that manipulated low price — satisfying its own manipulated `minPCOut` floor — before the attacker reverses their initial trade in a follow-up transaction, extracting the difference. This is functionally identical to the reported bug class: an exchange-rate/quote used to determine share-equivalent output is manipulable by an unprivileged actor because no invariant (minimum liquidity, TWAP, or external price bound) constrains the acceptable price range, and the resulting user deposit converts to far less PC than fair value.

### Impact Explanation
A successful sandwich against `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` causes the depositing user's PRC20-to-PC autoswap to execute at an attacker-manipulated price, resulting in a material, permanent, unrecoverable loss of value for that user's deposit — this squarely matches "loss for users who deposit" impact and the in-scope "corruption of ... gas fee accounting" / "PRC20 or native asset accounting" impact categories, since the amount of native PC the user's UEA receives for a given inbound GAS deposit is wrong and cannot be corrected after the fact (the swap already settled on-chain).

### Likelihood Explanation
The trigger requires only an unprivileged external actor with capital to trade against the specific Uniswap V3 pool used for the PRC20↔WPC pair, and the ability to time a transaction around the block in which the inbound reaches validator quorum — both of which are available to any chain user without compromising validators, TSS, or governance. Likelihood is higher for tokens with thinner on-chain liquidity in their configured fee-tier pool, and for larger inbound GAS amounts where the potential extracted value outweighs the attacker's capital and gas costs.

### Recommendation
Do not derive the enforced `minPCOut` solely from a spot quote fetched in the same call as the swap. Use a time-weighted average price (TWAP) over a meaningful window, cross-check against a secondary price source, and/or enforce a maximum allowed deviation from a recent moving-average/oracle price before allowing the swap to proceed; alternatively, require the pool to have a minimum liquidity depth (or cap the swap size as a fraction of pool liquidity) before permitting autoswap, and revert (retry later) rather than execute at a manipulated price when these checks fail.

### Proof of Concept
1. Attacker identifies the Uniswap V3 pool (`prc20 -> WPC`, at the fee tier returned by `defaultFeeTier`) used for a token's GAS autoswap.
2. Attacker submits a large swap into that pool, depressing the PRC20/PC spot price, timed to land in the same block as (or immediately before) the block in which a pending inbound `TxType_GAS`/`TxType_GAS_AND_PAYLOAD` reaches the 2/3+1 validator vote quorum that triggers `ExecuteInboundGas`.
3. `GetSwapQuote` returns the manipulated low price; `minPCOut = quote*95/100` is computed from it [7](#0-6) .
4. `CallPRC20DepositAutoSwap` executes the victim's deposit swap at the manipulated price, since it only needs to clear the already-depressed `minPCOut` floor.
5. Attacker reverses their initial trade in a follow-up transaction, restoring price and pocketing the value difference extracted from the victim's deposit — the victim's UEA receives far less PC than the pre-manipulation fair-market quote would have implied.

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
