This confirms the mechanism: `GetSwapQuote` reads a live Uniswap V3 pool spot price via `quoteExactInputSingle` (with `SqrtPriceLimitX96` unset), and `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` then applies a flat 5% slippage tolerance and executes the swap against that same manipulable pool.### Title
Sandwich-attackable Uniswap V3 spot-price quote enables MEV extraction on every gas-abstraction inbound deposit - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go)

### Summary
The Watchpug/Mellow analog is a value that is computed and trusted at one moment (a spot value) without protection against manipulation of that value between observation and use, letting an unprivileged attacker extract value that should belong to the depositor/protocol. In Push Chain, `ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` fetch a Uniswap V3 spot quote via `GetSwapQuote` (calling `QuoterV2.quoteExactInputSingle` with no `sqrtPriceLimitX96` protection) and then execute the actual swap through `CallPRC20DepositAutoSwap` against the same manipulable AMM pool, bounded only by a flat 5% slippage tolerance computed from that same spot price.

### Finding Description
For `TxType_GAS` and `TxType_GAS_AND_PAYLOAD` inbounds, the protocol converts the bridged PRC20 gas token into WPC/PC on behalf of the recipient UEA: [1](#0-0) 

The quote is fetched with `GetSwapQuote`, a read-only call into `QuoterV2.quoteExactInputSingle` against the live pool state, with `SqrtPriceLimitX96` set to zero (no price bound): [2](#0-1) 

`minPCOut` is then derived as a flat 95% of that just-fetched spot quote, and the swap is executed via `CallPRC20DepositAutoSwap`: [3](#0-2) [4](#0-3) 

The same pattern is used by `gasAndPayloadDepositAutoSwap` for `GAS_AND_PAYLOAD` inbounds: [5](#0-4) 

Because Push Chain's EVM mempool (ethermint-based) allows ordinary users to submit EVM transactions and have them included by gas-price priority within the same block as a `MsgVoteInbound` that triggers this deposit-and-swap path, an unprivileged attacker who observes the pending third (quorum-reaching) vote can:
1. Submit a swap transaction against the same PRC20/WPC Uniswap V3 pool immediately before the triggering `MsgVoteInbound`, depressing (or inflating) the pool price.
2. The `MsgVoteInbound` executes; `GetSwapQuote` reads the now-manipulated spot price, and `minPCOut` (95% of that manipulated quote) is computed and enforced against the same distorted price — so the 5% "slippage protection" offers no real protection, since both the reference quote and the execution price come from the same manipulated pool state within the same block.
3. The victim's autoswap executes at the manipulated price, and the attacker reverses their position in a follow-up transaction in the same or next block, capturing the price impact as profit at the expense of the UEA/recipient's expected PC output.

This mirrors the report's root cause: a value used to gate a fund-moving operation is computed from state that is trivially and cheaply manipulable by an unprivileged actor in the same execution window, with no TWAP, no oracle cross-check, and no protection against the observation itself being poisoned.

### Impact Explanation
Every `GAS` and `GAS_AND_PAYLOAD` inbound that routes through `depositPRC20WithAutoSwap` is subject to this MEV extraction, up to the bound of the 5% slippage tolerance per transaction (and potentially compounding if pool liquidity is thin, since the 5% is relative to a price the attacker themselves just set). This is a direct, repeatable value drain from ordinary depositing users to an unprivileged attacker, falling under "corruption of ... gas fee accounting ... token mapping ... canonical UniversalTx state" and unauthorized draining of user-controlled value during universal execution.

### Likelihood Explanation
High. No privileged role is required — only capital to briefly move the pool price and priority-fee EVM transactions to control ordering relative to the `MsgVoteInbound` that lands in the same block. The mechanism is systemic (fires on every gas-abstraction deposit), not a one-off edge case, and depends only on ordinary user/attacker-submitted EVM transactions plus honest validator behavior (no colluding validators needed).

### Recommendation
Do not derive `minPCOut` from a spot quote fetched in the same execution window as the swap. Use a time-weighted average price (TWAP) from the pool's oracle observations (Uniswap V3 supports this), or require an off-chain/validator-attested reference price with a tighter, justified slippage bound, or route protocol-owned autoswaps through a pool with restricted access (no public trading) so it cannot be manipulated by arbitrary third parties. At minimum, cross-check the QuoterV2 spot quote against a longer-window TWAP and reject/queue the swap if they diverge beyond a small threshold before computing `minPCOut`.

### Proof of Concept
1. Attacker observes 2 of 3 required `MsgVoteInbound` votes already submitted for a `TxType_GAS` inbound (visible on-chain/in mempool).
2. Attacker submits a large swap against the PRC20↔WPC Uniswap V3 pool (used by `GetSwapQuote`/`CallPRC20DepositAutoSwap`) with a higher gas price to land right before the third vote's block inclusion, depressing the PRC20→WPC price.
3. The third `MsgVoteInbound` reaches quorum in the same block; `ExecuteInboundGas` runs, calling `GetSwapQuote` (reads the manipulated price) then `CallPRC20DepositAutoSwap` with `minPCOut = quote * 0.95` (also based on the manipulated price), so the swap executes at the depressed rate without reverting.
4. Attacker immediately reverses their swap, restoring the price and pocketing the spread, while the recipient UEA receives up to ~5%+ less WPC/PC than it would have at the true market price.

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
