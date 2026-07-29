### Title
GAS-route auto-swap uses a spot-price Uniswap V3 quote with a protocol-fixed 5% slippage tolerance instead of a user-specified minimum output, enabling sandwich/front-run value extraction from inbound deposits - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/evm.go)

### Summary
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` compute the `minPCOut` slippage floor for a user's inbound gas-token → PC auto-swap by calling Uniswap V3's `QuoterV2.quoteExactInputSingle` at execution time and applying a hardcoded 5% tolerance (`quote * 95/100`). The user has no way to supply their own minimum acceptable output, the same root cause identified in the 88mph `DInterest.deposit` report where a sender cannot bound the output amount they will accept for a given input.

### Finding Description
`k.GetSwapQuote` [1](#0-0)  reads a live spot-price quote from the Uniswap V3 `QuoterV2` contract, and both `ExecuteInboundGas` [2](#0-1)  and `gasAndPayloadDepositAutoSwap` [3](#0-2)  derive `minPCOut` by applying a fixed 5% discount to that just-fetched quote before calling `CallPRC20DepositAutoSwap` [4](#0-3) , which invokes `depositPRC20WithAutoSwap` on `UniversalCore`.

`quoteExactInputSingle` reflects the current AMM pool tick/reserves at call time rather than a manipulation-resistant TWAP. The 5% band is a protocol-wide constant baked into the node code — it is not derived from, or bounded by, anything the originating user specified. An inbound's `Inbound`/`UniversalPayload` struct carries `maxFeePerGas`/`maxPriorityFeePerGas` for gas but no field for a user-chosen swap slippage tolerance or minimum output [5](#0-4) . This mirrors exactly the bug class in the external report: a component that converts a user's input amount into an output amount has no sender-supplied minimum-desired-output guard, only a protocol-fixed one, so state changes outside the user's control (an attacker's swap against the same AMM pool positioned around the moment ExecuteInboundGas runs) can degrade the executed price within the allowed 5% band without violating any on-chain check.

Because inbound execution occurs only after a ballot reaches quorum, and vote-finalization/execution timing is visible on Push Chain's public mempool/blocks, an unprivileged actor can watch for the finalizing vote and place ordinary transactions against the AMM pool (a swap in, then a swap back out) around that block to move the spot price the quote is drawn from, capturing the difference between the manipulated quote and the true fair price, up to the full 5% band, at the depositing user's expense.

### Impact Explanation
This corrupts the PRC20/native-asset accounting outcome of the deposit: the user receives materially less PC-denominated value than the fair-market conversion of their bridged funds would provide, and the difference is captured by whoever manipulated the pool around execution — a direct value-extraction/fund-loss impact on protocol/user-controlled funds during universal execution, matching the "corruption of ... gas token selection ... accounting" and "draining ... of user ... funds" impact categories in scope.

### Likelihood Explanation
Likelihood is bounded by the size of the pool and the 5% ceiling (the `require(interestAmount... )`-equivalent check on the contract side still stops manipulation beyond 5%), and by the attacker needing capital and gas to execute a sandwich around a specific, publicly-observable finalizing vote/block. It does not require any privileged role, validator collusion, or key compromise — any unprivileged party that can submit ordinary transactions can attempt it, so it is reachable via the default inbound-execution path with only unprivileged capabilities.

### Recommendation
- Short term: allow the source-chain inbound (or a companion field on `Inbound`/`UniversalPayload`) to carry a user-specified `minPCOut`/slippage tolerance, and require the value used in `CallPRC20DepositAutoSwap` to be at least as strict as the user's requested minimum, rather than always deriving it from a fixed 5% off a spot quote fetched at execution time.
- Long term: replace or supplement the spot `quoteExactInputSingle` call with a manipulation-resistant reference price (e.g., TWAP-based oracle) for computing the floor, and shrink the default slippage tolerance, consistent with the long-term recommendation in the source report to always give the value-receiving party control over their minimum acceptable output.

### Proof of Concept
1. Observe the Push Chain mempool/blocks for a `MsgVoteInbound` that will supply the final quorum vote for a pending `TxType_GAS` (or `GAS_AND_PAYLOAD`) inbound for a known gas-token PRC20/pool.
2. Submit an ordinary transaction (no privileged role required) that swaps a large amount into the same Uniswap V3 pool used by `GetSwapQuote`/`CallPRC20DepositAutoSwap` to move the pool's spot price, timed to land in the same or preceding block before the finalizing vote executes `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`.
3. When the vote finalizes, `GetSwapQuote` [1](#0-0)  reads the now-manipulated spot price, `minPCOut` is computed as 95% of that skewed quote [6](#0-5) , and `CallPRC20DepositAutoSwap` executes the victim's deposit swap at the degraded rate, still passing the on-chain `minPCOut` check.
4. Submit a follow-up transaction swapping back out of the pool to realize the captured spread, profiting from the difference between the fair price and the manipulated execution price of the victim's deposit — up to the 5% band — without any node/validator/relayer privilege.

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

**File:** x/uexecutor/types/inbound.go (L150-165)
```go
	// Validate fields required per tx_type
	switch p.TxType {
	case TxType_FUNDS_AND_PAYLOAD, TxType_GAS_AND_PAYLOAD:
		if p.UniversalPayload == nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "payload is required for payload tx types")
		}
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
		if err := p.UniversalPayload.ValidateBasic(); err != nil {
			return errors.Wrap(err, "invalid payload")
		}
	case TxType_FUNDS, TxType_GAS:
```
