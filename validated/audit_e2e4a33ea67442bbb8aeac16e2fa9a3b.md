Confirmed: `ExecuteInboundGas` executes synchronously inside the finalizing UV's `MsgVoteInbound` transaction (`x/uexecutor/keeper/msg_vote_inbound.go` step 8 calls `k.ExecuteInbound(ctx, utx)` directly in the same state transition that finalizes the ballot), and the swap quote used for slippage protection is fetched via `k.GetSwapQuote` (`x/uexecutor/keeper/evm.go:502-538`), which calls `QuoterV2.quoteExactInputSingle` against live pool state in the same block, with `minPCOut` set at a flat 5% tolerance off that same-block quote (`x/uexecutor/keeper/execute_inbound_gas.go:142-145`, `execute_inbound_gas_and_payload.go:374-376`).

### Title
Same-block spot-price swap quote with fixed 5% slippage lets an unprivileged attacker sandwich protocol-executed GAS/GAS_AND_PAYLOAD auto-swaps, draining bridged-user value - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
When a `GAS` or `GAS_AND_PAYLOAD` inbound reaches UV quorum, `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` fetch a Uniswap V3 `QuoterV2` spot quote and immediately execute `depositPRC20WithAutoSwap` with `minPCOut = quote * 95 / 100` in the very same transaction/block, with no TWAP or external price reference. An unprivileged attacker who front-runs the finalizing vote transaction with a large swap against the same pool can push the spot price down right before the quote is taken, causing the protocol's auto-swap (funded by the bridged user's deposited PRC20) to execute at the manipulated price and only be checked against a slippage bound derived from that same manipulated price. The attacker then reverses the trade, extracting the difference — a same-block "flashloan-style" analog to the sanRate exploit, where the honest distribution/execution logic references a manipulable, attacker-influenced on-chain quantity within a single block instead of a resistant reference price.

### Finding Description
The sanRate bug class is: a protocol distributes value (interest) based on a quantity (SanToken total supply) that an unprivileged attacker can inflate within the same block via a flashloan, and the safety cap is expressed relative to that same manipulable quantity, so the cap itself is gamed.

Push Chain's analog is structurally similar but on the swap-quote side: `GetSwapQuote` (`x/uexecutor/keeper/evm.go:500-538`) is a `CallEVM(..., commit=false, ...)` static call to `QuoterV2.quoteExactInputSingle`, which for Uniswap V3 reflects the *current* pool tick/sqrtPrice — i.e., a spot-price read, not a TWAP. This quote is used, in the same call, to compute `minPCOut` at a fixed 5% tolerance (`execute_inbound_gas.go:142-145`, `execute_inbound_gas_and_payload.go:374-376`), and then `CallPRC20DepositAutoSwap` executes the real swap against the same pool with that `minPCOut` as the only protection (`evm.go:540-593`).

Critically, this entire quote→swap sequence runs inside `ExecuteInboundGas`, which is invoked synchronously from `VoteInbound` the instant the ballot reaches quorum (`msg_vote_inbound.go:148-155`, calling `k.ExecuteInbound(ctx, utx)` → `ExecuteInboundGas`). Nothing about this dispatch is delayed, randomized, or protected by a commit-reveal — an attacker watching the mempool/UV vote pattern for a pending inbound (inbounds and their expected quorum are observable on-chain via `PendingInbounds`) can time a large swap on the Push Chain Uniswap V3 pool in a preceding transaction within the same block (or immediately before, since blocks and validator timing are predictable enough in practice), depress the quoted price of the PRC20 gas token, and then let the UV's finalizing vote trigger the deposit-and-autoswap at the depressed price. Because `minPCOut` is derived from that already-depressed quote, the swap still "succeeds" while returning far less WPC than fair value to the recipient/protocol. The attacker then reverses their swap, pocketing the extracted value.

This differs from ordinary sandwich risk on a user's own transaction (which the user could avoid) because here the swap is *forced* by protocol logic acting on the user's bridged deposit — the depositing user has no way to set their own slippage tolerance; the 5% bound is hardcoded and derived from the very quote the attacker manipulated.

### Impact Explanation
This directly enables value extraction from bridged user deposits/protocol-controlled funds: PRC20 tokens minted from a legitimate cross-chain deposit are auto-swapped into WPC at a price the attacker controls, and the shortfall (up to the full 5% band per manipulated call, and potentially more if the manipulated price itself moves the quote baseline) is captured by the attacker rather than delivered to the user's UEA. This is unauthorized value transfer from user/protocol-controlled funds during a core universal-execution flow (`GAS_AND_PAYLOAD`/`GAS` inbound auto-swap), matching the "stealing/draining ... of user or protocol-controlled funds" and "corruption of PRC20 or native asset accounting" impact categories in scope.

### Likelihood Explanation
Requires no privileged role — only capital to move the pool and the ability to submit an ordinary EVM swap transaction on Push Chain shortly before (or in the same block as) a UV's finalizing `MsgVoteInbound`. Pending inbounds and vote counts are observable on-chain (`PendingInbounds`), making the timing predictable rather than random. The size of the achievable profit scales with pool liquidity and how thin the underlying Uniswap V3 pool for that PRC20/WPC pair is — for pools with modest depth (plausible for many bridged PRC20 gas tokens early in their lifecycle), this is a realistic, repeatable attack, not a theoretical one.

### Recommendation
Replace the same-block spot quote with a manipulation-resistant reference (e.g., a TWAP over N blocks/observations, or an external oracle-checked bound) before computing `minPCOut`, and/or widen protection by comparing the spot quote against a recent moving-average price and rejecting/deferring the auto-swap if they diverge beyond a safety threshold. Consider also decoupling the swap's timing from the deterministic, publicly observable vote-finalization moment (e.g., execute in a later block using an already-committed price reference) to remove the attacker's ability to pre-position a sandwich.

### Proof of Concept
1. Attacker identifies a pending `GAS_AND_PAYLOAD` inbound close to UV quorum (visible via `PendingInbounds`/chain queries) for PRC20 token `T` with a shallow `T/WPC` Uniswap V3 pool on Push Chain.
2. Attacker submits a large `T → WPC` swap (or the reverse, depending on which direction depresses `T`'s quoted value) in a transaction that lands before the quorum-finalizing `MsgVoteInbound`.
3. The UV's finalizing vote triggers `ExecuteInboundGasAndPayload` → `gasAndPayloadDepositAutoSwap` → `GetSwapQuote` (spot, manipulated) → `CallPRC20DepositAutoSwap` with `minPCOut = quote*95/100` (also based on the manipulated quote).
4. The protocol's auto-swap executes at the depressed price for the full bridged deposit amount, and the recipient UEA receives WPC worth measurably less than the deposit's fair value.
5. Attacker reverses their initial swap, restoring the pool price and realizing the value difference as profit, extracted from the bridged user's funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
