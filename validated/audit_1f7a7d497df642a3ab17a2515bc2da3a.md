Now I have enough detail to write the analog finding.

The bug class from the report is: a swap that is subject to slippage protection can be forced to revert by an unprivileged attacker manipulating pool state around the swap, and — critically — there is no graceful fallback, so the whole enclosing operation (which should have delivered funds to the protocol/user) fails outright, and can be repeated to grief the flow indefinitely. The direct analog in Push Chain is `ExecuteInboundGas` / `ExecuteInboundGasAndPayload` in `x/uexecutor/keeper/execute_inbound_gas.go` and `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, which fetch a Uniswap V3 quote and then perform `depositPRC20WithAutoSwap` with a 5%-slippage `minPCOut`, but — unlike `applyGasRefund` in `x/uexecutor/keeper/outbound.go` which has an explicit no-swap fallback — has **no fallback path**: any swap revert is treated as a hard failure that forces `shouldRevert = true` and creates an `INBOUND_REVERT`.

### Title
Unprotected sandwich/slippage griefing on gas-abstraction autoswap forces repeated INBOUND_REVERT (no fallback, unlike gas-refund path) - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` (used by `ExecuteInboundGasAndPayload`) fetch a Uniswap V3 quote via `GetSwapQuote` and then call `CallPRC20DepositAutoSwap` → `depositPRC20WithAutoSwap` on `UniversalCore` with `minPCOut = quote*95/100` as slippage protection [1](#0-0) . Any user can submit ordinary swap transactions against the same underlying Uniswap V3 pool (used for PRC20↔WPC conversion) to move price against the pending gas-abstraction swap so that its execution reverts on the on-chain `minPCOut` check. Unlike the analogous `applyGasRefund` flow, which explicitly falls back to a no-swap PRC20 deposit when the swap leg fails [2](#0-1) , `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` have no such fallback: any swap failure is unconditionally treated as `shouldRevert = true`, producing an `INBOUND_REVERT` outbound and integration tests confirm this ("GAS inbound swap failure creates INBOUND_REVERT outbound") [3](#0-2) .

### Finding Description
`ExecuteInboundGas` runs the following sequence for every gas-abstraction (`TxType_GAS`) inbound, atomically inside vote finalization:
1. `GetSwapQuote` reads the current AMM-implied output for `amount` of `prc20AddressHex` (the deposited external gas token) into WPC.
2. `minPCOut` is derived as 95% of that quote (fixed 5% slippage tolerance).
3. `CallPRC20DepositAutoSwap` performs the real swap on the same pool via `UniversalCore.depositPRC20WithAutoSwap`, reverting on-chain if the received amount is below `minPCOut`. [4](#0-3) 

Because the swap is executed against a real, shared Uniswap V3 pool that also backs ordinary user swaps, an unprivileged actor can submit transactions in the mempool that move the pool price between arbitrary points in time, and, since Push Chain has a public mempool and predictable finalization (vote reaches quorum → execution happens in the same message handling), an attacker watching for pending `MsgVoteInbound`/quorum-reaching transactions for `TxType_GAS`/`TxType_GAS_AND_PAYLOAD` inbounds can push the pool price so that by the time `depositPRC20WithAutoSwap` executes, the realized output falls under `minPCOut`, causing the deposit-and-swap call itself to revert on-chain.

Critically, the code path treats this failure exactly like a fatal validation error: `execErr != nil` sets `shouldRevert = true` unconditionally, and the entire inbound is redirected into `buildRevertOutbound` / `attachOutboundsToUtx`, producing an `INBOUND_REVERT` [5](#0-4) . There is no attempt to fall back to a no-swap PRC20 deposit, unlike the parallel refund logic in `applyGasRefund`, which explicitly retries with `withSwap=false` on swap failure [6](#0-5) . `gasAndPayloadDepositAutoSwap`, used by `ExecuteInboundGasAndPayload` for both the UEA and smart-contract-recipient branches, has the identical no-fallback pattern [7](#0-6) .

The practical effect: an attacker can repeatedly grief any GAS or GAS_AND_PAYLOAD gas-abstraction inbound by keeping the pool price adversarial around the time of finalization, forcing the protocol to always take the `INBOUND_REVERT` path instead of successfully crediting the user's UEA with native gas. This is analogous to the audited `BuyBackBurner`/Slipstream report's core lesson: a swap operation with hard revert-on-slippage and no graceful degradation, sitting on a path reachable by any unprivileged actor manipulating a shared AMM pool, becomes a repeatable denial-of-service vector against protocol/user fund delivery.

### Impact Explanation
Every affected inbound is forced through the `INBOUND_REVERT` path instead of completing the intended gas-abstraction deposit, meaning the user's gas-abstraction swap into PC native funds never happens as intended; instead the bridged tokens are routed back to the source chain as a revert. This is a denial-of-service against a core universal-execution flow (gas abstraction), reachable by an ordinary unprivileged user manipulating a public AMM pool — no admin/validator/relayer collusion required. It degrades UX and increases operational load (repeated reverts, extra outbound TSS signing rounds) but, per the existing revert-and-remint safety net, does not directly result in fund loss since bridged funds are minted back on revert (`buildRevertOutbound`) [8](#0-7) .

### Likelihood Explanation
Likelihood depends on how liquid/thin the specific PRC20↔WPC pool is and on the attacker's ability to time transactions relative to inbound finalization; on thin pools this is straightforward and cheap to repeat (the attacker's own manipulating trade can typically be reversed after triggering the revert, similar to the original report's "attacker calls refundETH to reclaim ETH" pattern). This makes it a realistic, repeatable griefing vector rather than a one-off edge case.

### Recommendation
Add the same graceful degradation used in `applyGasRefund` to `ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap`: on swap failure (slippage revert), retry with `depositPRC20WithAutoSwap`'s no-swap-equivalent path (or `depositPRC20Token` directly) instead of unconditionally reverting the inbound. Alternatively/additionally, widen or dynamically compute slippage tolerance from pool liquidity, and/or use a TWAP-based quote to reduce single-block/single-quote manipulation exposure.

### Proof of Concept
1. Identify a gas-abstraction (`TxType_GAS`) inbound about to reach validator quorum for a PRC20/WPC pool with modest liquidity.
2. Submit an ordinary swap transaction against that same pool to move the price such that a subsequent `depositPRC20WithAutoSwap` call for the attacked inbound's amount will realize less than 95% of the pre-swap quote.
3. Once the validators' `MsgVoteInbound` quorum transaction executes `ExecuteInboundGas`, `CallPRC20DepositAutoSwap` reverts inside `depositPRC20WithAutoSwap` due to `minPCOut` not being met.
4. Observe `execErr != nil` and `shouldRevert = true` unconditionally routes the inbound to `INBOUND_REVERT` (confirmed by existing test `execute_inbound_gas_test.go` "GAS inbound swap failure creates INBOUND_REVERT outbound") [9](#0-8) , instead of falling back to a plain deposit as `applyGasRefund` does for the symmetric outbound-refund case.
5. Repeat for subsequent gas-abstraction inbounds targeting the same pool to sustain the griefing.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L166-208)
```go
	if execErr != nil {
		k.Logger().Warn("execute inbound gas: swap failed",
			"utx_key", universalTxKey,
			"error", execErr.Error(),
			"should_revert", shouldRevert,
		)
		pcTx.ErrorMsg = execErr.Error()
	} else {
		k.Logger().Info("execute inbound gas: swap succeeded",
			"utx_key", universalTxKey,
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
		pcTx.Status = "SUCCESS"
	}

	// --- Update UniversalTx always
	updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
		utx.PcTx = append(utx.PcTx, &pcTx)
		return nil
	})
	if updateErr != nil {
		// if state update fails, revert the tx
		return updateErr
	}

	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			revertReason,
		); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```

**File:** x/uexecutor/keeper/outbound.go (L213-245)
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

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
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

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-25)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
		Amount:            inbound.Amount,
		ExternalAssetAddr: inbound.AssetAddr,
		Sender:            inbound.Sender,
		TxType:            types.TxType_INBOUND_REVERT,
		OutboundStatus:    types.Status_PENDING,
		Id:                types.GetOutboundRevertId(inbound.SourceChain, inbound.TxHash, inbound.LogIndex),
	}
```

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L272-305)
```go
	t.Run("GAS inbound swap failure creates INBOUND_REVERT outbound", func(t *testing.T) {
		// When the autoswap fails ExecuteInboundGas sets shouldRevert=true and creates
		// an INBOUND_REVERT outbound so the user's funds are returned.
		chainApp, ctx, vals, inbound, coreVals := setupInboundGasTest(t, 4)

		reachGasQuorum(t, ctx, chainApp, vals, coreVals, inbound, 3)

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)

		// There should be at least one INBOUND_REVERT outbound
		foundRevert := false
		for _, ob := range utx.OutboundTx {
			if ob.TxType == uexecutortypes.TxType_INBOUND_REVERT {
				foundRevert = true
				require.Equal(t, inbound.SourceChain, ob.DestinationChain,
					"revert outbound destination must match inbound source chain")
				require.Equal(t, inbound.Amount, ob.Amount,
					"revert outbound amount must match inbound amount")
				require.Equal(t, inbound.AssetAddr, ob.ExternalAssetAddr,
					"revert outbound asset must match inbound asset")
				require.Equal(t, uexecutortypes.Status_PENDING, ob.OutboundStatus,
					"revert outbound should start in PENDING status")

				// Gas fields are populated from UniversalCore if chain meta is set.
				// In test env without VoteChainMeta, they may be zero/empty — that's OK,
				// the outbound is still created (graceful degradation).
				// When chain meta IS set, these will be populated.
				break
			}
		}
		require.True(t, foundRevert, "INBOUND_REVERT outbound should be created when swap fails")
```
