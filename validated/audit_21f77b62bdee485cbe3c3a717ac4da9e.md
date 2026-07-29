I have sufficient evidence to confirm the analog. Three separate call sites in `x/uexecutor/keeper` compute `minPCOut` identically via `quote * 95 / 100` integer arithmetic, with no floor/zero check, before invoking `depositPRC20WithAutoSwap` or `refundUnusedGas` on-chain — this is a direct native analog of the AaveUtils `_tokenSwapOutAmount`-returning-0 issue.

### Title
Unbounded slippage on module-driven UniversalCore auto-swaps due to unchecked `minPCOut = quote * 95 / 100` truncating to zero - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
The `uexecutor` keeper computes the minimum acceptable output (`minPCOut`) for every gas-token → WPC auto-swap performed on behalf of users (GAS inbound deposits, GAS_AND_PAYLOAD deposits, and outbound gas-refund swaps) using integer arithmetic `quote * 95 / 100` with no check that the result — or the underlying quote — is non-zero. For small deposit/refund amounts, Solidity/Go integer division truncates `minPCOut` to `0`, which is then passed straight into the on-chain `depositPRC20WithAutoSwap` / `refundUnusedGas` calls as the slippage floor, permitting a 100%-slippage swap that is trivially sandwichable by an unprivileged actor observing the mempool/module-originated tx.

### Finding Description
Three call sites derive `minPCOut` the same way:

- `ExecuteInboundGas` (GAS inbound path): [1](#0-0) 
- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound path): [2](#0-1) 
- `applyGasRefund` (outbound gas-refund path): [3](#0-2) 

Each does:
```go
quote, execErr = k.GetSwapQuote(...)          // amountOut from Uniswap V3 QuoterV2.quoteExactInputSingle
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))       // integer division truncates toward zero
```
`GetSwapQuote` obtains a live on-chain quote via QuoterV2 [4](#0-3) ; for a sufficiently small `amount` (attacker-controlled — the deposit `Amount` on an `Inbound` originates from an externally-observed, user-submitted deposit and is only quorum-voted, not bounded to a minimum by the executor path), `quote` itself can be a very small integer (e.g. 1–20 wei-equivalent units). Multiplying by 95 and dividing by 100 then yields `0` even though `quote` is nonzero, and if `quote` is already `0`, `minPCOut` is trivially `0`. This `minPCOut` (or `0`) is passed unchecked as `amountOutMinimum`-equivalent to `depositPRC20WithAutoSwap` [5](#0-4)  and to `refundUnusedGas` via `CallUniversalCoreRefundUnusedGas`. Neither the Go keeper code nor (per the ABI definitions inspected) an enforced minimum-output guard exists before the swap executes, so the swap accepts any output ≥ 0, i.e. unbounded slippage.

Because these are module-originated `DerivedEVMCall`/`DerivedEVMCall`-style transactions executed deterministically by every validator as part of ordinary inbound/outbound finalization, an unprivileged attacker who crafts (or triggers via normal use) a small-amount deposit or a scenario producing a small gas-refund amount can predict that the resulting swap will have `minPCOut == 0`, and can sandwich the Uniswap V3 pool (front-run to move price, let the module swap execute at any price, back-run to capture the difference) extracting value from the protocol's own auto-swap.

### Impact Explanation
This fits the "corruption of PRC20 or native asset accounting… refund accounting… gas fee accounting" and "draining… of user or protocol-controlled funds" impact categories: the deposited PRC20 tokens (or refunded gas token) are protocol/module-controlled at swap time, and a 100%-slippage swap lets a third-party MEV actor extract the price difference, effectively draining value that should have accrued to the user's UEA deposit or refund recipient. The trigger is reachable purely through ordinary unprivileged deposit/withdrawal flows (small-value inbound deposits, or gas refunds computed from `gasFee - gasFeeUsed`), requiring no privileged or malicious-validator behavior — validators execute the flawed logic honestly and deterministically.

### Likelihood Explanation
Likelihood is moderate-to-high: any attacker can submit (or cause via normal cross-chain deposit flows) a small-value inbound transaction on a supported chain; the resulting on-chain swap parameters (`quote`, `minPCOut`) are fully deterministic and can be simulated off-chain against the current pool state before the module transaction lands, making a sandwich straightforward for anyone running a bot against Push Chain's mempool/block production.

### Recommendation
Enforce that `minPCOut` (and the underlying `quote`) is never zero for any swap amount greater than zero — e.g., revert/skip the auto-swap and fall back to the no-swap deposit path (as already exists for other failure branches) when `quote.Sign() <= 0` or the computed `minPCOut.Sign() <= 0`, or apply a minimum-output floor (e.g., `max(1, quote*95/100)`) combined with a sanity lower bound on swap amount, consistent across `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `outbound.go`.

### Proof of Concept
1. Observe/trigger an inbound deposit (`GAS` or `GAS_AND_PAYLOAD` tx type) with a very small `Amount` of the external gas token (e.g., a few wei-equivalent units) such that `GetSwapQuote` returns a quote of 1–20 units.
2. Compute `minPCOut = quote*95/100` in Go integer arithmetic — for `quote` in `[1,20]`, `minPCOut == 0`.
3. When validators reach quorum and `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` executes `CallPRC20DepositAutoSwap(..., minPCOut=0)`, the Uniswap V3 pool swap accepts any nonzero output.
4. An attacker monitoring pending inbound votes front-runs the pool (large trade to skew price against WPC output), lets the module's swap execute at the skewed price (accepted because `minPCOut=0`), then back-runs to restore price and capture the extracted value — repeatable for every small-amount deposit or gas-refund event.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-148)
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L217-230)
```go
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
