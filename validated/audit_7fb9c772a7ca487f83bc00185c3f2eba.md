### Title
Failed auto-swap/deposit in isCEA inbound flows permanently strands user funds - no revert path exists - (File: `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
The FairSide bug is a class of "downstream swap call fails, and the caller has no fallback, so the whole flow breaks and funds get stuck." Push Chain's `x/uexecutor` inbound-execution keeper has a structurally analogous but more severe issue: for `isCEA` inbound flows (`GAS_AND_PAYLOAD` and `FUNDS_AND_PAYLOAD`), if the PRC20 deposit or auto-swap call to `UniversalCore` fails, the code deliberately skips creating an `INBOUND_REVERT` outbound, permanently stranding the user's bridged funds.

### Finding Description
In `ExecuteInboundGasAndPayload`, the isCEA branch calls `gasAndPayloadDepositAutoSwap` (which internally calls Uniswap `QuoterV2.quoteExactInputSingle` then `UniversalCore.depositPRC20WithAutoSwap` with a computed `minPCOut`), but on failure it explicitly does **not** set `shouldRevert`: [1](#0-0) 

The comment makes the intent explicit: "isCEA failures never create an INBOUND_REVERT outbound." Further down, failures for isCEA are recorded as a `FAILED` PCTx and the function returns `nil` without ever building a revert outbound: [2](#0-1) 

This is in stark contrast with the non-isCEA branch of the exact same function, where a deposit/auto-swap failure does set `shouldRevert = true` and attaches an `INBOUND_REVERT` outbound so the user's funds are sent back on the source chain: [3](#0-2) [4](#0-3) 

The identical pattern (deposit failure with no revert for isCEA) exists in `ExecuteInboundFundsAndPayload`: [5](#0-4) [6](#0-5) 

An integration test even documents the asymmetry, confirming that only the non-CEA path builds a revert outbound on swap failure: [7](#0-6) 

The auto-swap itself (`gasAndPayloadDepositAutoSwap`) fetches a fresh Uniswap V3 quote and derives `minPCOut` with only 5% slippage tolerance at execution time: [8](#0-7) 

Because block production/vote-tallying timing is attacker-observable and pool prices can move between the `quoteExactInputSingle` read and the `depositPRC20WithAutoSwap` commit (e.g., via any pool-price-moving transaction in the mempool, or simply organic pool volatility/thin liquidity for a given PRC20/WPC pair), the swap can legitimately revert on `minPCOut` slippage-check failure, or fail because `GetDefaultFeeTierForToken`/`GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress` are unset/misconfigured for a given token. None of these failure conditions require any privileged actor — they can be hit by an ordinary, honest sender simply bridging funds in via the `isCEA` (Cross-Chain Execution Address) path.

### Impact Explanation
When the auto-swap/deposit fails for an `isCEA` inbound, the source-chain funds the user already locked/sent (observed and voted on by honest validators) are never credited to any Push Chain address, and — unlike the equivalent non-CEA code path — no `INBOUND_REVERT` outbound is ever created to send them back. The `UniversalTx` is simply left with a `FAILED` `PCTx` entry and the function returns `nil`, meaning the inbound is treated as terminally handled. This is a permanent, unrecoverable loss of user funds triggered purely by ordinary use of the isCEA transfer/payload flow, matching the "Allowed Impact Gate" for permanent freezing/loss of user funds via unauthorized state transitions in the universal execution flow, reachable by an unprivileged external user.

### Likelihood Explanation
Likelihood is high for any token pair with thin Uniswap liquidity, an unset/misconfigured `defaultFeeTier`, or during normal price volatility, since the auto-swap slippage window is only 5% and the quote-then-commit is not atomic within the same on-chain state as when the quote was fetched (separate `CallEVM` staticcall for `GetSwapQuote` versus the `DerivedEVMCall` commit). Any user using the isCEA GAS_AND_PAYLOAD / FUNDS_AND_PAYLOAD entrypoint with such a token is exposed, and it requires no validator, TSS, or governance misbehavior — only ordinary honest-validator observation of an ordinary user's cross-chain deposit.

### Recommendation
Treat isCEA deposit/auto-swap failures the same as the non-CEA path: set `shouldRevert = true` and build/attach an `INBOUND_REVERT` outbound (or another explicit refund mechanism) so bridged funds are always returned to the sender when the on-chain deposit/swap cannot complete, rather than being silently marked `FAILED` with no recovery path.

### Proof of Concept
1. A user sends an isCEA `GAS_AND_PAYLOAD` (or `FUNDS_AND_PAYLOAD`) inbound transfer of a PRC20-mapped asset with thin Uniswap liquidity (or during a period where the on-chain quote diverges from the swap-time price by >5%) to a valid UEA recipient.
2. Honest validators vote and the inbound reaches quorum; `ExecuteInboundGasAndPayload`/`ExecuteInboundFundsAndPayload` executes.
3. `gasAndPayloadDepositAutoSwap`/`depositPRC20`'s underlying `CallPRC20DepositAutoSwap` call to `UniversalCore.depositPRC20WithAutoSwap` reverts because the realized swap output is below `minPCOut` (or fee-tier/quoter lookups fail).
4. `execErr != nil`, the isCEA branch skips `shouldRevert`, records a `FAILED` PCTx, and the function returns `nil` at the isCEA-failure guard.
5. No `INBOUND_REVERT` outbound is ever created — the user's already-locked source-chain funds are permanently unrecoverable, as demonstrated by the asymmetric test coverage confirming the non-CEA path alone builds a revert outbound on the identical failure.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L76-100)
```go
						// UEA path: deposit + autoswap into the UEA (if amount > 0), then execute payload via UEA
						if amount.Sign() > 0 {
							prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
							receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
							if execErr != nil {
								execErr = fmt.Errorf("depositAutoSwap failed: %w", execErr)
							}
						}
					} else {
						// Non-UEA: check if recipient has code (smart contract) vs EOA
						codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
						if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
							isSmartContract = true
						}
						// EOA: just deposit, skip executeUniversalTx
						if amount.Sign() > 0 {
							prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
							receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
							if execErr != nil {
								execErr = fmt.Errorf("depositAutoSwap failed: %w", execErr)
							}
						}
					}
				}
				// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L146-153)
```go
					if execErr == nil && amount.Sign() > 0 {
						// --- Step 4 & 5: deposit + autoswap (only when amount > 0)
						prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
						receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L190-209)
```go
	// --- create revert ONLY for pre-deposit / deposit failures (non-isCEA path)
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, utx.InboundTx)

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

		return nil
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L211-214)
```go
	// isCEA failures: record FAILED PCTx but no revert
	if execErr != nil && utx.InboundTx.IsCEA {
		return nil
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-103)
```go
	if utx.InboundTx.IsCEA {
		// isCEA path: recipient is explicitly specified.
		// Three-way check:
		//   1. Recipient is a UEA  → existing flow (deposit + ExecutePayloadV2)
		//   2. Recipient is a deployed smart contract (not UEA) → deposit + executeUniversalTx
		//   3. Neither → record FAILED PCTx, no INBOUND_REVERT
		if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
			execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
		} else {
			ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)

			_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
			if ueaCheckErr != nil {
				execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
			} else if isUEA {
				// UEA path: deposit PRC20 into the UEA (if amount > 0), then execute payload via UEA
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
			} else {
				// Non-UEA: check if recipient has code (smart contract) vs EOA
				codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
				if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
					// Smart contract: will call executeUniversalTx after deposit
					isSmartContract = true
				}
				// EOA: just deposit, skip executeUniversalTx (no contract to call)
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
			}
		}
		// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L187-206)
```go
	// If deposit failed, stop here.
	if execErr != nil {
		if shouldRevert {
			revertOutbound := k.buildRevertOutbound(sdkCtx, utx.InboundTx)
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
		return nil
	}
```

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L272-306)
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
	})
```
