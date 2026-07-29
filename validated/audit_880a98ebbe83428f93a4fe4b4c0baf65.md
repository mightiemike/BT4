## Finding

### Title
Spot-price-based `minPCOut` slippage guard in gas-to-native auto-swap paths is sandwichable by an unprivileged attacker manipulating the Uniswap V3 pool — (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
This is the same bug class as the HydraDX omnipool `remove_liquidity` finding: a swap/withdrawal path relies on an in-built slippage tolerance computed from a manipulable spot price rather than protecting the user against pre-existing price manipulation. Push Chain's `GAS`/`GAS_AND_PAYLOAD` inbound auto-swap and the outbound unused-gas refund swap fetch a live, spot AMM quote via `GetSwapQuote` (a Uniswap V3 `QuoterV2.quoteExactInputSingle` call) and derive `minPCOut = quote * 95 / 100`, then immediately execute the swap against that same pool.

### Finding Description
`GetSwapQuote` reads the current pool state on demand: [1](#0-0) 

The 5% cushion is applied on top of whatever the pool state is *at the moment of the call*, not a price the user or protocol agreed upon beforehand: [2](#0-1) [3](#0-2) [4](#0-3) 

Both `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` (triggered once an inbound ballot is finalized by honest validators, executed within ordinary block processing) and `applyGasRefund` (triggered on outbound-vote finalization) call this quote-then-swap pattern using the on-chain `defaultFeeTier` Uniswap pool. Because both the quote and the swap happen against the *same*, currently-manipulable pool, an unprivileged actor can submit ordinary swap transactions on Push Chain's native DEX pool for the relevant PRC20/WPC pair immediately before the block/transaction that triggers this deposit-auto-swap or refund-swap, artificially moving the spot price, then reverse the trade immediately after — a classic sandwich. The 5% band only bounds movement *after* the quote is taken; it does nothing to prevent the attacker from having already moved the price before the quote call, exactly mirroring how the omnipool bug's 1%/2% `ensure_price` band bounded post-quote movement but not pre-existing frontrunning.

### Impact Explanation
A depositor bridging native gas funds (GAS / GAS_AND_PAYLOAD inbound) or a relayer/sender expecting an unused-gas refund receives systematically worse PRC20↔native conversion rates whenever an attacker sandwiches the auto-swap. Value is transferred from the honest user/protocol account to the attacker via the AMM pool, which falls squarely under "corruption of ... gas fee accounting, refund accounting ... token mapping" and unauthorized-loss-of-user-funds impact categories, reachable purely by an ordinary unprivileged user submitting swap transactions against the public Uniswap pool — no privileged access, malicious validator, or malicious relayer required.

### Likelihood Explanation
The QuoterV2 pool address, `defaultFeeTier`, and PRC20 addresses are all discoverable on-chain, and inbound funds-with-gas transactions (and outbound gas refunds) are routine, expected traffic. Any actor who can watch the mempool/pending inbound votes or simply run periodic sandwich bots against the known PRC20/WPC pools can extract value whenever a sizeable auto-swap occurs, making this a realistic, repeatable MEV vector rather than a purely theoretical one — consistent with how the upstream finding was accepted as a valid Medium (MEV) issue.

### Recommendation
Do not rely solely on a percentage band derived from a live spot quote taken in the same execution as the swap. Use a manipulation-resistant reference price (e.g., a TWAP oracle, or a governance/registry-configured maximum acceptable price deviation from a recent moving average) to bound `minPCOut`, and/or cap the swap size or defer/split large auto-swaps to reduce single-block price impact. At minimum, tighten and make the slippage tolerance configurable/auditable rather than hardcoded at 5%, and consider requiring confirmation that the quote and execution occur with negligible reentrancy/ordering risk relative to attacker-controlled transactions in the same block.

### Proof of Concept
1. Attacker observes an inbound `GAS`/`GAS_AND_PAYLOAD` transaction (or an outbound awaiting `MsgVoteOutbound` finalization with a nonzero gas-fee excess) about to trigger `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`/`applyGasRefund`.
2. Attacker submits a large swap on the PRC20/WPC Uniswap V3 pool (`defaultFeeTier` pool from `GetDefaultFeeTierForToken`) just before the block containing the ballot-finalizing transaction, pushing the spot price against the victim's expected direction.
3. When `GetSwapQuote` is called inside `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund`, it returns the already-manipulated price; `minPCOut = quote*95/100` is computed from this bad price.
4. `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` executes at this unfavorable rate (within the manipulated 5% band, so it does not revert).
5. Attacker reverses their initial swap in a following transaction in the same or next block, capturing the price-impact profit at the expense of the deposited/refunded funds. [5](#0-4)

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

**File:** x/uexecutor/keeper/outbound.go (L213-234)
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
```
