## Title
Zero-floor `minPCOut` from integer-truncated slippage math removes swap protection in module auto-swap deposits and gas refunds - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report shows `wstUSR.previewWithdraw` truncating an integer division to `0`, which silently strips away the vault's rounding-up safety and lets the withdraw path proceed with no required burn. Push Chain's `uexecutor` keeper has the same class of bug: the 5%-slippage `minPCOut` guard for module-originated Uniswap V3 swaps is computed as `quote * 95 / 100` using Go's `big.Int` integer division, which truncates to `0` whenever `quote` is small enough. When that happens, the DEX-swap slippage protection silently disappears and the module executes the swap with `minPCOut = 0`, i.e. "accept any output."

### Finding Description
Three call sites compute the swap's minimum-acceptable-output the same way:

- `ExecuteInboundGas` (auto-swap deposit for `GAS`/`GAS_AND_PAYLOAD` inbounds): [1](#0-0) 

- `gasAndPayloadDepositAutoSwap` (auto-swap deposit for `GAS_AND_PAYLOAD` payload flow): [2](#0-1) 

- `applyGasRefund` (swap-back refund of unused destination-chain gas): [3](#0-2) 

In all three, `quote` comes from a live `QuoterV2.quoteExactInputSingle` call against the on-chain Uniswap V3 pool: [4](#0-3) 

`minPCOut` is then passed straight into `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas(withSwap=true)`, which perform the actual swap via `DerivedEVMCall` against the `UniversalCore`/handler contract: [5](#0-4) [6](#0-5) 

Because `quote * 95 / 100` uses integer division, any `quote` in the range `[0, 20]` (in the smallest PC unit) floors to `minPCOut = 0`. There is no floor check, no minimum-quote check, and no fallback to reject/require a nonzero `minPCOut` before the deposit/refund swap is dispatched. `ValidateForExecution` only requires the inbound `Amount` to be non-negative (and positive except for the payload tx types), with no minimum-value threshold that would keep `quote` above the truncation boundary: [7](#0-6) 

An unprivileged attacker who submits a very small-value cross-chain deposit (or whose deposit happens to price out to a tiny `quote` under prevailing pool conditions) causes the module's own `DerivedEVMCall`-issued Uniswap V3 swap to run with zero slippage protection. Since (a) `MsgVoteInbound` quorum for a given inbound takes multiple blocks/votes to finalize, and (b) the pool used (`prc20`/gas-token ↔ `WPC`) is an ordinary on-chain AMM reachable by any user's ordinary swap transactions, an attacker can manipulate the pool price immediately around the module's swap execution (classic sandwich) and capture nearly all of the swap output that should have gone to the depositor's UEA (in `CallPRC20DepositAutoSwap`) or to the refund recipient (in `applyGasRefund`).

### Impact Explanation
This misroutes protocol/user-controlled value during PRC20 accounting: the deposit or refund recipient receives far less PC than the true market value of the swapped tokens, while the value is extracted via price manipulation of the module-controlled AMM leg. This corrupts PRC20/native-asset accounting and results in unauthorized loss of user/protocol funds during a module-originated EVM execution path — squarely in the "Registry and accounting path" and "corruption of ... token mapping ... must not misroute value" allowed-impact categories. It is reachable purely through ordinary user deposit/inbound and outbound-refund flows with only honest validators/nodes involved.

### Likelihood Explanation
Medium. The attacker doesn't need any privileged role — only the ability to submit a small cross-chain deposit (or wait for one to occur) and to place ordinary swap transactions around the multi-block window in which the module's derived swap executes. The precise threshold (`quote <= 20` smallest units) is narrow but deterministic and computable in advance from the quoter, so an attacker can specifically target inbound amounts that produce a near-zero quote, or wait for pool price movement to push a modest-sized deposit's quote under the threshold.

### Recommendation
- Compute `minPCOut` using rounding that never floors below a meaningful floor, e.g. round up (`ceil`) instead of truncating down, and additionally enforce `minPCOut > 0` (or a configurable minimum) before dispatching the swap.
- Alternatively, reject/skip the auto-swap path (falling back to a plain non-swap deposit) when `quote` is below a safe threshold, rather than silently proceeding with an unprotected swap.
- Apply the same fix uniformly to `ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund`.

### Proof of Concept
1. Attacker identifies (via the public `QuoterV2.quoteExactInputSingle`/on-chain pool state) an inbound `amount` of `X` external-token units that currently quotes to `quote <= 20` PC-smallest-units.
2. Attacker submits a `GAS` or `GAS_AND_PAYLOAD` inbound with `Amount = X` on the source chain; honest Universal Validators vote it in over several blocks via `MsgVoteInbound`.
3. Just before/at the block where the vote reaches quorum and `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` fires the module's `depositPRC20WithAutoSwap` derived call, the attacker (or a colluding account) executes an ordinary swap on the same Uniswap V3 pool to push the effective price against the module's swap.
4. Because `minPCOut = quote*95/100 = 0`, the module's swap succeeds despite receiving far less PC than the un-manipulated quote implied.
5. The attacker reverses their price-manipulating swap afterward, netting the value siphoned from the deposit; the legitimate depositor's UEA receives a PC amount far below fair value.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-148)
```go
						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L374-378)
```go
	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L218-223)
```go
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
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

**File:** x/uexecutor/keeper/evm.go (L574-592)
```go
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

**File:** x/uexecutor/keeper/evm.go (L624-643)
```go
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
```

**File:** x/uexecutor/types/inbound.go (L126-138)
```go
func (p Inbound) ValidateForExecution() error {
	// Validate amount as uint256
	if strings.TrimSpace(p.Amount) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty")
	}
	bi, ok := new(big.Int).SetString(p.Amount, 10)
	if !ok || bi.Sign() < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid non-negative uint256")
	}
	// Only GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD allow zero amount (skip deposit, still execute payload)
	if bi.Sign() == 0 && p.TxType != TxType_GAS_AND_PAYLOAD && p.TxType != TxType_FUNDS_AND_PAYLOAD {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be positive for this tx type")
	}
```
