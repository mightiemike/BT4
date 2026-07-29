## Title
On-chain AMM spot-price used as both quote and slippage bound in inbound/outbound PRC20↔WPC auto-swaps enables sandwich-attack fund extraction - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The Teller Finance bug is a class of vulnerability where a manipulable, same-transaction share/price value is used both to compute an expected output *and* the slippage tolerance protecting it, letting an attacker who controls that price manipulate it first and then pass the "protection" check while draining value. Push Chain's `x/uexecutor` module reproduces the same anti-pattern in its Uniswap-V3-style auto-swap logic used for inbound gas top-ups, inbound gas+payload execution, and outbound gas refunds: `GetSwapQuote` fetches a live spot quote from the on-chain `QuoterV2` contract and the code then derives `minPCOut` as a flat 95% of that same, freshly-manipulable quote.

### Finding Description
`Keeper.GetSwapQuote` performs a live, same-block call to `QuoterV2.quoteExactInputSingle` against the on-chain Uniswap-V3-style pool that converts PRC20 tokens to WPC: [1](#0-0) 

Every call site then computes the slippage floor purely as a percentage of this same spot quote, with no independent reference price, TWAP, or externally supplied minimum: [2](#0-1) [3](#0-2) [4](#0-3) 

Because the pool referenced by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress` is a standard tradable AMM inside Push Chain's EVM, an unprivileged actor can submit an ordinary transaction that swaps against this pool immediately before the module-originated `depositPRC20WithAutoSwap` / `refundUnusedGas` call executes in the same or an adjacent block. This moves the pool's spot price. When the executor keeper subsequently calls `GetSwapQuote`, it reads the already-manipulated price and derives `minPCOut = quote * 95/100` from it — the check only guards against *further* drift within that single call, not against the attacker's prior manipulation. The executor then performs the deposit-and-swap or refund-and-swap at the corrupted price via `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`: [5](#0-4) 

This is structurally identical to the Teller M-4 root cause: a value that is both attacker-influenceable and used to self-certify its own correctness ("slippage protection" computed from the manipulated value itself), enabling extraction of the difference between fair value and the manipulated execution price. The attacker completes the sandwich by reversing their initial trade against the pool after the module's swap lands, capturing the spread. Victims are (a) end users whose deposited PRC20 mints less WPC than it should during `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`, and (b) the refund recipient (often the original sender) in `applyGasRefund`, whose unused-gas refund is executed at a manipulated rate.

### Impact Explanation
This falls within the allowed impact scope: it corrupts "gas fee accounting, refund accounting ... and canonical UniversalTx state" and allows an unprivileged external attacker to steal value from user deposits and gas refunds processed through `x/uexecutor`'s honest-validator-driven, user-reachable inbound/outbound flows — no privileged party is required, only ordinary transaction submission against a public AMM pool plus normal inbound deposit/outbound refund traffic that the module already produces automatically for every user.

### Likelihood Explanation
Likelihood is material: any user (or the attacker acting as depositor) triggers `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` merely by making a normal cross-chain deposit, and every successful outbound with excess gas automatically triggers `applyGasRefund`. The attacker only needs the ability to submit ordinary EVM swaps against the on-chain quoter's pool, which is expected to be permissionless liquidity infrastructure, before/around the block in which the module executes its swap — a standard MEV/sandwich pattern requiring no validator or admin privilege.

### Recommendation
Do not derive both the expected amount and its own tolerance from the same live, attacker-reachable spot quote. Use a time-weighted average price (TWAP) or an independently sourced reference price to bound acceptable output, and/or require the calling flow (or a validator-attested value from ballot finalization) to carry an authenticated `minPCOut`/expected-price bound set at intent-creation time rather than recomputed from current pool state immediately before execution. Additionally consider restricting or rate-limiting trading against the WPC conversion pool, or routing conversions through a price oracle resistant to single-block manipulation.

### Proof of Concept
1. Attacker observes the PRC20↔WPC pool referenced by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress` has shallow liquidity.
2. Attacker submits a large swap PRC20→WPC (or WPC→PRC20) against that pool to push the spot price in the direction that reduces WPC output for a subsequent PRC20 deposit.
3. Attacker (or any user) triggers a cross-chain deposit that flows into `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`; the keeper calls `GetSwapQuote` (evm.go:500-538), which reads the manipulated price, and computes `minPCOut = quote*95/100` (execute_inbound_gas.go:142-148).
4. `CallPRC20DepositAutoSwap` executes the swap at the manipulated price, well within the "slippage protected" 95% bound of the already-corrupted quote, minting far less WPC than fair value to the victim's UEA.
5. Attacker reverses their initial trade against the pool, capturing the price spread — net profit at the expense of the depositing user/protocol. The identical pattern applies to `applyGasRefund`'s swap-based refund path in `outbound.go:213-237`.

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

**File:** x/uexecutor/keeper/outbound.go (L213-237)
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
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}
```
