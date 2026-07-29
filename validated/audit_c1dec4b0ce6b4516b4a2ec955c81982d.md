The scoped repository contains a close structural analog to this TON bug in the `isCEA` smart-contract inbound execution path.

## Title
Attacker-controlled inbound recipient/payload can permanently strand minted PRC20 tokens when the post-deposit notification call fails or rolls back - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
The TON report's root cause is: value is delivered to a recipient contract's balance, but the "notification" call that lets the contract account for/act on that value is suppressed or fails, and there is no fallback path (return to caller/refund) — so the funds are stuck forever. Push Chain's `isCEA` (arbitrary EVM contract recipient) inbound path has the same two-step "deposit-then-notify" structure, with the deposit committed to real state before the notify call, and the notify call wrapped in a `CacheContext` that is silently discarded on failure, while the isCEA branch is explicitly documented to never create a revert/rescue outbound.

### Finding Description
For CAIP-2 EVM inbounds marked `IsCEA=true` whose `Recipient` resolves to a deployed non-UEA smart contract, both `ExecuteInboundFundsAndPayload` and `ExecuteInboundGasAndPayload` follow the same two-phase sequence:

1. Mint/deposit PRC20 to the recipient contract address, committed directly against `sdkCtx` (not cached): [1](#0-0) 
2. Separately, inside a `CacheContext`, call `CallExecuteUniversalTx` (the "notification" to the contract) followed by gas-fee deduction; the cache is only written back if *both* succeed: [2](#0-1) 

If `CallExecuteUniversalTx` itself errors (e.g., the target has no matching selector, reverts, or runs out of gas) or the post-call `DeductGasFeesFromReceipt` fails (recipient has no `upc` to pay gas), the entire cache — i.e., the notify call's state changes — is discarded, while the earlier PRC20 deposit remains committed: [3](#0-2) 

Crucially, the code explicitly documents and tests that the isCEA path never creates an `INBOUND_REVERT` outbound on this kind of failure: [4](#0-3)  and the integration test suite asserts this as intended behavior, confirming tokens remain deposited in the recipient while `executeUniversalTx` is rolled back with no rescue outbound: [5](#0-4) 

Both `Recipient` and `UniversalPayload` (including `Data`, `GasLimit`) are attacker-controlled inbound fields observed and voted on by honest Universal Validators — the validators only attest to what happened on the source chain, not to whether the destination-side notify call will later succeed. An attacker can pick any already-deployed EVM contract on Push Chain as `Recipient` (not necessarily one they control) and craft a payload/gas limit that is guaranteed to make `CallExecuteUniversalTx` fail or make fee deduction fail (e.g., a contract with zero `upc` balance and no fallback to acquire it). The PRC20 deposit still lands on that contract's balance permanently, with no mechanism (contract has no expectation of holding these tokens, and the protocol offers no revert/refund/retry).

### Impact Explanation
This matches the "permanent freezing of user or protocol-controlled funds" and "corruption of PRC20 ... accounting" impact categories: value (minted PRC20) becomes permanently unrecoverable at an address that never validly received/acknowledged it and has no means to move it out, reachable purely through ordinary inbound submission/voting by honest validators observing an attacker-chosen source-chain event.

### Likelihood Explanation
Triggering requires only: (1) a real inbound deposit on a supported source chain routed with `IsCEA=true` to an arbitrary deployed Push Chain contract, and (2) a payload/gas configuration that causes `CallExecuteUniversalTx` or the subsequent fee deduction to fail — both fully attacker-controlled and reachable via the standard inbound voting flow with honest validators.

### Recommendation
Mirror the TON report's short-term fixes: either (a) don't commit the PRC20 deposit until after `CallExecuteUniversalTx` succeeds (wrap deposit + notify + fee deduction in a single cache, and on any leg failing, refund/route to `RevertInstructions.FundRecipient` instead of leaving it on the contract), or (b) explicitly create a rescue/revert outbound for isCEA smart-contract-path failures instead of the current documented "isCEA failures never create an INBOUND_REVERT" behavior. Pre-validate that the recipient can pay for `executeUniversalTx` gas before minting.

### Proof of Concept
1. Deploy (or select) a Push Chain contract with zero `upc` balance and no way to receive `upc` from calldata (e.g., the `mockRecipientContractAddr` STOP-opcode contract used in tests).
2. Submit/vote an inbound with `IsCEA=true`, `Recipient` = that contract, arbitrary `Amount`, and a `UniversalPayload` — reach quorum via honest UVs as in `setupInboundCEASmartContractTest`.
3. Because the contract holds no `upc`, `DeductGasFeesFromReceipt` fails after `CallExecuteUniversalTx`, causing the cache to be discarded — but the PRC20 deposit (Step 1, outside the cache) is already committed, as demonstrated in `TestInboundCEASmartContractRecipient/fee_deduction_failure_rolls_back_executeUniversalTx,_keeps_deposit` [6](#0-5) .
4. No `INBOUND_REVERT` outbound is created (asserted explicitly by the test), and the contract has no function to move out the PRC20 balance it never expected — funds are stuck permanently.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L89-100)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L233-283)
```go
				// Wrap the EVM call + fee deduction in a CacheContext so they
				// commit/revert together. If fee deduction fails, the EVM state
				// changes from executeUniversalTx are discarded — closes the
				// free-execution gap when the recipient contract has no native
				// UPC to cover gas. The deposit (above this scope) stays
				// committed regardless.
				cacheCtx, writeCache := sdkCtx.CacheContext()
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
			}
		}

		callPcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
			Status:      "FAILED",
		}
		if contractReceipt != nil {
			callPcTx.TxHash = contractReceipt.Hash
			callPcTx.GasUsed = contractReceipt.GasUsed
		}
		switch {
		case contractErr != nil:
			callPcTx.ErrorMsg = contractErr.Error()
		case feeErr != nil:
			callPcTx.ErrorMsg = fmt.Sprintf("gas fee deduction failed: %s", feeErr.Error())
		default:
			callPcTx.Status = "SUCCESS"
		}
		if updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &callPcTx)
			return nil
		}); updateErr != nil {
			return updateErr
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

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L354-480)
```go
	// F-2026-16738: when DeductGasFeesFromReceipt fails after a successful
	// CallExecuteUniversalTx, the EVM call + fee deduction now run inside a
	// CacheContext that is discarded on fee failure. The deposit (which
	// happens before this scope) stays committed; the executeUniversalTx
	// state changes are rolled back so the recipient cannot consume gas
	// without paying for it.
	t.Run("fee deduction failure rolls back executeUniversalTx, keeps deposit", func(t *testing.T) {
		chainApp, ctx, vals, _, coreVals, _ := setupInboundCEASmartContractTest(t, 4)

		// Deploy a recipient whose payload mutates EVM storage (slot 0
		// counter +1 on every call). Lets us prove the payload ran AND
		// was rolled back by reading storage post-execution.
		recipientAddr := deployStatefulRecipientContract(t, chainApp, ctx)

		// Sanity-check the storage starts at zero.
		slot := common.Hash{}
		preState := chainApp.EVMKeeper.GetState(ctx, recipientAddr, slot)
		require.Equal(t, common.Hash{}, preState, "stateful recipient slot 0 must start at zero")

		// Recipient has zero native upc balance → DeductGasFeesFromReceipt
		// will fail with insufficient funds.
		recipientAccAddr := sdk.AccAddress(recipientAddr.Bytes())
		balanceBefore := chainApp.BankKeeper.GetBalance(ctx, recipientAccAddr, "upc")
		require.True(t, balanceBefore.Amount.IsZero(), "recipient must start with zero upc balance for this test")

		usdcAddress := utils.GetDefaultAddresses().ExternalUSDCAddr
		testAddress := utils.GetDefaultAddresses().DefaultTestAddr
		statefulInbound := &uexecutortypes.Inbound{
			SourceChain: "eip155:11155111",
			TxHash:      "0xsc-fee-fail-01",
			Sender:      testAddress,
			Recipient:   recipientAddr.String(),
			Amount:      "1000000",
			AssetAddr:   usdcAddress.String(),
			LogIndex:    "1",
			TxType:      uexecutortypes.TxType_FUNDS_AND_PAYLOAD,
			UniversalPayload: &uexecutortypes.UniversalPayload{
				To:                   recipientAddr.String(),
				Value:                "1000000",
				Data:                 "0xdeadbeef",
				GasLimit:             "21000000",
				MaxFeePerGas:         "1000000000",
				MaxPriorityFeePerGas: "200000000",
				Nonce:                "1",
				Deadline:             "9999999999",
				VType:                uexecutortypes.VerificationType(1),
			},
			VerificationData: "",
			IsCEA:            true,
			RevertInstructions: &uexecutortypes.RevertInstructions{
				FundRecipient: testAddress,
			},
		}

		// Vote tx must succeed even though fee deduction fails internally —
		// only the cached executeUniversalTx state is discarded; the rest
		// of the SDK tx commits.
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, statefulInbound)
			require.NoError(t, err, "vote tx should succeed even when fee deduction fails internally")
		}

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*statefulInbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found, "UTX must exist (vote completed atomically)")
		require.GreaterOrEqual(t, len(utx.PcTx), 2, "should have deposit + executeUniversalTx PCTxs")

		// Deposit succeeded — happened BEFORE the cache scope, so it persists.
		depositPcTx := utx.PcTx[0]
		require.Equal(t, "SUCCESS", depositPcTx.Status, "deposit PCTx should succeed (outside cache scope)")
		require.Empty(t, depositPcTx.ErrorMsg)

		// callPcTx records the fee-deduction failure with the canonical prefix.
		callPcTx := utx.PcTx[1]
		require.Equal(t, "FAILED", callPcTx.Status, "callPcTx Status should record fee deduction failure")
		require.Contains(t, callPcTx.ErrorMsg, "gas fee deduction failed",
			"ErrorMsg must carry the canonical 'gas fee deduction failed' prefix")

		// EVM call DID execute (and was measured) before the cache was discarded.
		// TxHash + GasUsed are returned values from the call; they survive even
		// though the state changes were rolled back.
		require.NotEmpty(t, callPcTx.TxHash, "EVM tx hash should be captured even when fee fails")
		require.Greater(t, callPcTx.GasUsed, uint64(0), "EVM execution consumed gas (proves it ran in the cache)")

		// THE PRIMARY ASSERTION: the recipient's payload code did NOT mutate
		// committed EVM storage. Slot 0 stayed at 0 because the cache holding
		// the SSTORE was discarded. Proves the CacheContext rollback worked.
		postState := chainApp.EVMKeeper.GetState(ctx, recipientAddr, slot)
		require.Equal(t, common.Hash{}, postState,
			"recipient storage slot 0 must remain 0 (proves executeUniversalTx state was rolled back)")

		// Deposit (above the cache scope) committed atomically with the rest
		// of the SDK tx — recipient holds the PRC20 tokens.
		prc20ABI, err := uexecutortypes.ParsePRC20ABI()
		require.NoError(t, err)
		prc20Address := utils.GetDefaultAddresses().PRC20USDCAddr
		ueModuleAccAddress, _ := chainApp.UexecutorKeeper.GetUeModuleAddress(ctx)

		res, err := chainApp.EVMKeeper.CallEVM(
			ctx, prc20ABI, ueModuleAccAddress, prc20Address, false, nil, "balanceOf", recipientAddr,
		)
		require.NoError(t, err)
		balances, err := prc20ABI.Unpack("balanceOf", res.Ret)
		require.NoError(t, err)
		require.Len(t, balances, 1)
		expectedAmount := new(big.Int)
		expectedAmount.SetString(statefulInbound.Amount, 10)
		require.Equal(t, 0, balances[0].(*big.Int).Cmp(expectedAmount),
			"PRC20 balance must be deposited to recipient (deposit was outside the rolled-back cache scope)")

		// No fee was actually collected (cache discarded → no bank debit).
		balanceAfter := chainApp.BankKeeper.GetBalance(ctx, recipientAccAddr, "upc")
		require.Equal(t, balanceBefore.Amount, balanceAfter.Amount,
			"recipient upc balance unchanged (cache discarded; no fee collected)")

		// Rescue path correctly does not fire: it checks PcTx[0].Status which
		// is SUCCESS, so no INBOUND_REVERT outbound is created.
		for _, ob := range utx.OutboundTx {
			require.NotEqual(t, uexecutortypes.TxType_INBOUND_REVERT, ob.TxType,
				"no INBOUND_REVERT should be created (deposit succeeded)")
		}
	})
```
