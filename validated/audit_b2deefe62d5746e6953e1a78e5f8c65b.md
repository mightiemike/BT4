Confirmed: the swap pool is a real Uniswap-V3-style pool (`uniswapV3Quoter` / `QuoterV2`) on Push Chain itself, and the entire quote→swap sequence runs synchronously inside `ExecuteInboundGasAndPayload`, which is itself invoked directly from `VoteInbound`/`MsgVoteInbound` the instant the finalizing (quorum) vote lands — all in one Cosmos transaction/block [1](#0-0) . This is the concrete native analog to the AUM sandwich.

### Title
GAS_AND_PAYLOAD/GAS auto-swap uses a manipulable spot AMM quote with fixed 5% slippage, enabling sandwich extraction of protocol/user deposited funds - (File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
For `GAS_AND_PAYLOAD` and `GAS` inbound types, when a cross-chain deposit needs to be converted from a PRC20 asset into native WPC, the module fetches a spot price quote from an on-chain Uniswap V3-style `QuoterV2` pool and immediately executes the real swap using that quote with a hardcoded 5% slippage tolerance — all inside the same Cosmos transaction that finalizes the triggering `MsgVoteInbound` vote. An unprivileged attacker who observes the finalizing vote transaction in the mempool can sandwich this swap (manipulate the pool price before it lands, let the module's autoswap execute at the manipulated price, then reverse the manipulation), extracting value from the depositor's/protocol's swapped funds up to the 5% slippage bound — directly analogous to the reported AUM sandwich pattern (a periodically/atomically-refreshed price value consumed for value conversion, sandwichable by watching the triggering transaction).

### Finding Description
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` (called from `ExecuteInboundGasAndPayload`) both perform the identical unsafe pattern:

1. `GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` (`commit=false`) against the pool at `quoterAddr` to get the current expected output for swapping the deposited PRC20 amount into WPC [2](#0-1) . This is a live spot-price read of an on-chain, presumably low/medium liquidity AMM pool (`uniswapV3Quoter` address configured in `UniversalCore`) [3](#0-2) .
2. Immediately afterward, `minPCOut` is derived as `quote * 95 / 100` — a fixed 5% slippage tolerance, with no TWAP, no oracle cross-check, and no protection against the quote and the swap execution being manipulated between the two calls [4](#0-3) [5](#0-4) .
3. `CallPRC20DepositAutoSwap` then executes the actual on-chain swap via `depositPRC20WithAutoSwap` at handler contract, transferring real value out of the module/pool [6](#0-5) .
4. Crucially, this entire quote+swap sequence executes synchronously inside the SAME Cosmos SDK transaction that carries the quorum-finalizing `MsgVoteInbound` vote: `VoteInbound` calls `k.ExecuteInbound(ctx, utx)` directly once the ballot finalizes, with no intervening block boundary [1](#0-0) .

Because the finalizing vote (typically the validator whose vote reaches the 2/3+1 threshold) is broadcast like any ordinary transaction, an unprivileged attacker monitoring the mempool can identify it ahead of inclusion (same technique as watching the bot's AUM-update transaction in the seed report) and construct a sandwich:
- Front-run: submit a large swap in the same Uniswap V3 pool (PRC20↔WPC) to move the spot price in the attacker's favor.
- The validator's finalizing vote transaction executes next in the block, triggering `GetSwapQuote` against the now-manipulated pool and then the real `depositPRC20WithAutoSwap` swap, which executes at the distorted price (bounded only by the generous, hardcoded 5% slippage tolerance).
- Back-run: reverse the initial swap, capturing the price impact/arbitrage profit that was extracted from the module's swap.

This mirrors the reported bug class precisely: a periodically-refreshed price/value used to convert one asset amount into another for user-facing settlement is fetched and consumed atomically by a state-changing transaction whose timing is externally observable and front-runnable, with no protections (same-block restriction, delay, or tightened bound) preventing extraction.

### Impact Explanation
Every `GAS` or `GAS_AND_PAYLOAD` cross-chain deposit that goes through the PRC20-to-WPC autoswap path is exposed. Since the swapped amount belongs to the depositing user (their bridged funds being converted for gas), a successful sandwich directly drains value from the user's/protocol's cross-chain deposit into the attacker's pocket, up to the full 5% slippage tolerance per swap. This is a real, repeatable value-extraction vector reachable by an ordinary unprivileged user with no special access, matching "corruption of PRC20/native asset accounting" and "misrouted value" in the allowed-impact scope.

### Likelihood Explanation
Likelihood is Medium: it requires (a) visibility into the mempool for the finalizing `MsgVoteInbound` transaction and (b) sufficient capital/flash-loan access relative to the pool's liquidity to move the spot price meaningfully within the 5% band. Both conditions are realistic for MEV-capable actors on any chain with a public mempool, and every `GAS_AND_PAYLOAD`/`GAS` inbound with a non-trivial deposit amount is a candidate target, making this a systemic, recurring exposure rather than a one-off edge case.

### Recommendation
- Replace the single spot `quoteExactInputSingle` call with a time-weighted average price (TWAP) read from the pool, or otherwise require the quote to be resistant to single-block manipulation.
- Tighten the slippage tolerance (5% is very wide) and/or make it configurable per token/liquidity depth.
- Where possible, decouple "get quote" from "execute swap" so they cannot be manipulated within the same block/transaction (e.g., require quote and execution to be separated by at least one committed block, or use a commit-reveal style two-step swap).
- Consider capping the swap size relative to observed pool depth, or routing through a protocol-owned oracle-informed price rather than raw spot AMM state.

### Proof of Concept
1. Attacker monitors the Push Chain mempool for a `MsgVoteInbound` carrying a `GAS_AND_PAYLOAD`/`GAS` inbound that is one vote away from quorum (visible because prior votes for the same `utx_key` are already on-chain/pending).
2. Attacker submits, ahead of the finalizing vote tx in the same block, a large swap against the WPC/PRC20 Uniswap V3 pool referenced by `uniswapV3Quoter`/`defaultFeeTier`, moving the spot price to disadvantage the upcoming module swap.
3. The finalizing `MsgVoteInbound` lands; `VoteInbound` → `ExecuteInbound` → `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` runs `GetSwapQuote` against the now-skewed pool and executes `CallPRC20DepositAutoSwap` with `minPCOut = quote*95/100`, settling the deposited PRC20 for WPC at the distorted price [7](#0-6) .
4. Attacker back-runs with the reverse swap in the same block, unwinding their position and capturing the value difference extracted from the module's swap — functionally identical to the AUM sandwich's "deposit before / withdraw after" pattern, but expressed against an on-chain AMM spot quote instead of an off-chain-updated AUM value.

### Citations

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

**File:** x/uexecutor/keeper/evm.go (L422-444)
```go
// GetUniversalCoreQuoterAddress reads the uniswapV3Quoter address stored in UniversalCore.
func (k Keeper) GetUniversalCoreQuoterAddress(ctx sdk.Context) (common.Address, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "uniswapV3Quoter")
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to call uniswapV3Quoter")
	}

	results, err := abi.Methods["uniswapV3Quoter"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to unpack uniswapV3Quoter result")
	}

	return results[0].(common.Address), nil
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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L374-378)
```go
	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```
