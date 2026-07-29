## Analog Found — Sandwichable AMM Swap in Gas-Token Auto-Swap / Refund Path

### Title
Unauthenticated slippage-tolerance sandwich attack on PRC20 auto-swap deposit and gas-refund swap paths - ([File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go], [File: x/uexecutor/keeper/outbound.go], [File: x/uexecutor/keeper/evm.go])

### Summary
The referrer report's core bug class is: a reward/value calculation is derived from *live, instantaneously-manipulable state* with no time-weighting or manipulation resistance, letting an attacker sandwich the state right before the reward-triggering transaction to extract value. Push Chain's `uexecutor` module contains a structurally identical pattern in its gas-token auto-swap logic: every inbound `GAS`/`GAS_AND_PAYLOAD` deposit and every outbound gas refund swaps a PRC20 gas token for native PC using a **spot quote fetched from the live Uniswap-v3-style pool** (`GetSwapQuote` → `quoteExactInputSingle`), with only a fixed 5% slippage tolerance computed from that same spot quote, and no TWAP/oracle protection.

### Finding Description
`k.GetDefaultFeeTierForToken` and `k.GetSwapQuote` ( [1](#0-0) ) are called immediately before the actual swap executes via `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` ( [2](#0-1) ). In every call site — `ExecuteInboundGas` ( [3](#0-2) ), `gasAndPayloadDepositAutoSwap` ( [4](#0-3) ), and `applyGasRefund` ( [5](#0-4) ) — the slippage bound is computed the same way:

```
minPCOut := quote * 95 / 100
```

`quote` is a synchronous, un-timelocked spot price read from the on-chain AMM pool at the moment the module executes the swap. This is analogous to the referrer bug's flaw: a value (`fromReferrer`'s score / here, the pool's spot price) that can be freely and atomically manipulated by an unprivileged actor immediately before the protocol reads and acts on it, and the protocol has no mechanism (time-weighting, private execution, commit-reveal) to prevent that manipulation from being captured for profit.

The trigger transactions that cause this swap to execute are:
- `MsgVoteInbound` reaching quorum (bridging deposit finalization) — a **gasless** message type ( [6](#0-5) ), meaning it is broadcast to, and visible in, the public mempool before inclusion, exactly like the original report's "referrer monitors the tx pool" step.
- `MsgVoteOutbound` reaching quorum for the gas-refund swap path.

An unprivileged external attacker (any EOA with an EVM account on Push Chain, no privileged role) who observes a pending finalizing vote for a bridged gas-token deposit can, within the same block or the immediately preceding block:
1. Trade against the PRC20/WPC pool to push the spot price against the pending swap direction (front-run).
2. Let the module's auto-swap execute at the worst price still inside the 5% band (the module only guards against a price *worse than 5%*, not against manipulated *baseline* price, since `quote` itself was fetched after the attacker's manipulation).
3. Reverse the trade (back-run), capturing the spread as profit extracted directly from the user's bridged deposit / refund value.

This is a corruption of PRC20 accounting/value routing reachable purely through ordinary, unprivileged user actions (public EVM trades + the natural mempool visibility of gasless vote messages), matching the in-scope impact category "corruption of PRC20 or native asset accounting... must not misroute value."

### Impact Explanation
Every bridged gas-token deposit (`GAS`, `GAS_AND_PAYLOAD` inbound types) and every outbound gas refund with excess gas is subject to up to ~5% value leakage per occurrence, extracted by the sandwiching attacker at the expense of the depositing/refunded user. Given this runs on every qualifying inbound/outbound across all supported external chains, the aggregate value at risk is material and continuous, not a one-off. Funds are not permanently frozen, but they are silently drained from users to an attacker via mispriced swaps executed by protocol-owned module logic — a direct, repeatable value-transfer bug.

### Likelihood Explanation
Medium-to-high: the attack requires no privileged role, no validator collusion, and no cryptographic break — only (a) capital to move the pool price and (b) mempool visibility of the pending finalizing `MsgVoteInbound`/`MsgVoteOutbound`, both of which are ordinary, unprivileged capabilities on any public chain. The fixed, uniform 5% tolerance combined with a spot (non-TWAP) quote is a textbook sandwich setup.

### Recommendation
- Use a time-weighted average price (TWAP) or a price oracle resistant to single-block manipulation instead of an instantaneous `quoteExactInputSingle` spot quote for computing `minPCOut`.
- Tighten and/or dynamically size the slippage tolerance based on pool liquidity/observed volatility rather than a flat 5%.
- Consider routing gas-token swaps through a protected execution path (e.g., batched/delayed execution, or a private/aggregated settlement) so pending swap direction and size are not predictable/front-runnable from the public mempool.
- At minimum, monitor and cap per-block swap volume through this path to bound attacker profit per manipulation window.

### Proof of Concept
1. Attacker observes 3 of 4 validators have already broadcast (gasless, public-mempool) `MsgVoteInbound` votes for a `GAS` or `GAS_AND_PAYLOAD` inbound bridging a PRC20 gas token, one vote short of quorum.
2. Attacker submits a large trade against the same PRC20/WPC pool moving the spot price unfavorably for the upcoming module-initiated swap direction, and pays to have it mined immediately before the finalizing vote.
3. The finalizing `MsgVoteInbound` lands, `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` calls `GetSwapQuote` (now reflecting the manipulated price) and executes `CallPRC20DepositAutoSwap` with `minPCOut = quote*95/100`, filling at the manipulated (but still "in-tolerance") price.
4. Attacker reverses the initial trade in the same or next block, capturing the spread — value that would otherwise have accrued to the PC amount credited for the bridging user's deposit (or refund recipient). [7](#0-6) [8](#0-7)

### Citations

**File:** x/uexecutor/keeper/evm.go (L470-538)
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

**File:** x/uexecutor/keeper/evm.go (L540-644)
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

**File:** x/uexecutor/keeper/outbound.go (L178-237)
```go
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

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

**File:** app/txpolicy/gasless.go (L14-26)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```
