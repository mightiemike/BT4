## Analysis

The reported bug class is "swap functions call AMM router without real slippage protection, allowing flash-loan/sandwich price manipulation to steal swapped value." Push Chain has a native analog in the gas-abstraction auto-swap path used by `x/uexecutor`, where inbound "gas" deposits (PRC20 tokens bridged in to pay for gas) are auto-swapped into WPC via a Uniswap-V3-style router/quoter pair that lives on-chain (`UniversalCore`/`QuoterV2`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Unlike Origin's `minAmountOut = 0`, this code does compute a nonzero `minPCOut` (5% below `GetSwapQuote`'s result), but the quote itself is fetched from `QuoterV2.quoteExactInputSingle`, a spot-price read of the same AMM pool, in the same call path immediately before the swap executes — there is no TWAP, no user-/off-chain-supplied bound, and no block-delay between quoting and swapping. [5](#0-4) 

The trigger for this swap is `VoteInbound`, invoked whenever a validator's `MsgVoteInbound` finalizes the inbound ballot — this is a normal, publicly broadcast Cosmos transaction, so an outside observer can watch the mempool, predict when the last vote will land, and manipulate the target WPC/PRC20 pool immediately before/after that transaction in the same or adjacent block (classic sandwich), just as described for `harvest`/`allocate` in the original report. [6](#0-5) 

Because the "slippage protection" bound is itself derived from the same manipulable spot price at execution time, it only protects against price movement *after* the quote is fetched, not against an attacker who has already skewed the pool before the quote call — the same underlying flaw as the original C01 report (protection computed from the attacker-influenced spot price rather than an independent/resistant reference).

### Title
Sandwichable spot-price auto-swap in gas-abstraction inbound processing lets an attacker extract value from user PRC20→WPC deposits - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go)

### Summary
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` compute `minPCOut` from `GetSwapQuote`, which reads a live spot price off the on-chain Uniswap-V3-style pool (`QuoterV2.quoteExactInputSingle`) in the same call immediately preceding `CallPRC20DepositAutoSwap`. Because the reference price and the executed swap both occur atomically against the same manipulable pool, an unprivileged attacker can sandwich the triggering `MsgVoteInbound` transaction (or the pool state right before it lands) to extract value from the user's bridged-in PRC20 deposit that is auto-swapped into WPC gas token.

### Finding Description
`GetSwapQuote` fetches `amountOut` via a simulated call to the `QuoterV2` contract, which reflects the pool's current spot price/tick. [1](#0-0) 

`ExecuteInboundGas` (fee-abstraction gas deposit) and `gasAndPayloadDepositAutoSwap` (gas+payload deposit) then derive `minPCOut = quote * 95 / 100` from that same live quote and pass it straight into `CallPRC20DepositAutoSwap`, which performs the swap via `depositPRC20WithAutoSwap` on the `UniversalCore` handler contract. [7](#0-6) [8](#0-7) 

The same pattern is used for the outbound gas-token refund swap quote. [4](#0-3) 

This swap is not initiated by the depositing user — it is executed automatically by the module when a validator's `MsgVoteInbound` finalizes the inbound ballot, inside the same state-transition as ballot finalization. [6](#0-5) 

Because `MsgVoteInbound` transactions are ordinary, publicly gossiped Cosmos SDK transactions, an unprivileged attacker monitoring the mempool can predict exactly when a ballot is about to finalize (e.g., seeing enough validator votes accumulated) and then:
1. Front-run the finalizing vote transaction with a large swap that moves the PRC20/WPC (or gas-token/WPC) pool price.
2. Let the finalizing transaction execute `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`, which quotes and swaps against the now-skewed price — the 5% band is computed relative to the attacker-manipulated price, not the fair price, so it does not prevent the manipulation.
3. Back-run with the reverse trade to restore the pool and realize a profit extracted from the value that should have gone to the depositing user's UEA as WPC gas token.

This mirrors the exact bug class from the external report: a nonzero-but-manipulable reference price used as the "slippage" bound, computed atomically from an attacker-influenced AMM at execution time, rather than a resistant reference (TWAP, external oracle, or a caller-supplied bound independent of module-side spot reads).

### Impact Explanation
Each affected inbound (gas abstraction deposit, gas+payload deposit, and gas-token refund) converts user-owned PRC20/native value into WPC through an atomically-quoted swap. An attacker can systematically skim value from these conversions across many inbound transactions, draining value from users' gas deposits/refunds into their own pocket — this is unauthorized value extraction/misrouting of user-controlled funds during a core universal-execution flow (gas abstraction), matching the in-scope "corruption of ... gas fee accounting, refund accounting ... must not misroute value" and "stealing ... of user or protocol-controlled funds" impacts. The magnitude scales with pool liquidity depth and the volume/frequency of gas-abstraction inbound traffic, similar to how the original report noted profit scales with TVL.

### Likelihood Explanation
The trigger conditions require only: (1) a PRC20/WPC (or gas-token/WPC) pool with limited liquidity relative to attacker capital, and (2) the ability to observe pending `MsgVoteInbound` transactions in the mempool or otherwise predict/react to their timing, both of which are available to any unprivileged, non-validator actor. No malicious validator, relayer, or governance action is required — the honest validators behave correctly and simply finalize the ballot as designed; the vulnerability sits purely in how the module derives its own slippage bound.

### Recommendation
Replace the atomic spot-price quote used as the slippage reference with a manipulation-resistant reference, e.g.:
- Use a time-weighted average price (TWAP) from the pool instead of `QuoterV2`'s instantaneous quote, or
- Enforce a maximum deviation between the current spot price and a longer-window observed price before allowing the auto-swap to proceed, or
- Defer/require an independently-supplied or governance-configured minimum-output bound rather than one derived from the same block's on-chain state, and/or add per-block/tx price-impact circuit breakers around `GetSwapQuote`/`CallPRC20DepositAutoSwap` and `getSwapQuoteForRefund`.

### Proof of Concept
1. Attacker monitors the mempool for `MsgVoteInbound` transactions destined for a chain/token pair with a shallow PRC20/WPC pool, and identifies one that will complete the required validator quorum (finalizing the ballot).
2. Immediately before that transaction is included, attacker submits a large swap in the same PRC20/WPC pool to depress the PRC20 price relative to WPC.
3. The finalizing `MsgVoteInbound` executes `VoteInbound` → `ExecuteInbound` → `ExecuteInboundGas`, which calls `GetSwapQuote` against the now-depressed pool and computes `minPCOut = quote * 95/100` off that skewed price, then calls `CallPRC20DepositAutoSwap`, swapping the user's deposited PRC20 for an amount of WPC far below fair value.
4. Attacker back-runs with the reverse trade to restore the pool price, pocketing the difference between the fair value of the user's PRC20 deposit and the depressed WPC amount actually received by the user's UEA.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L104-153)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L259-270)
```go
// getSwapQuoteForRefund fetches a Uniswap quote for the gas token refund swap.
func (k Keeper) getSwapQuoteForRefund(ctx sdk.Context, gasToken common.Address, fee *big.Int, amount *big.Int) (*big.Int, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(ctx)
	if err != nil {
		return nil, err
	}
	wpcAddr, err := k.GetUniversalCoreWPCAddress(ctx)
	if err != nil {
		return nil, err
	}
	return k.GetSwapQuote(ctx, quoterAddr, gasToken, wpcAddr, fee, amount)
}
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
