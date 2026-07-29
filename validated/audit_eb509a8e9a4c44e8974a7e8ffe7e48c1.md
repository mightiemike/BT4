This is confirmed and matches the code precisely, with the codebase's own test (F-2026-16738 marker) already documenting the exact behavior described in the question.

### Title
Attacker-controlled recipient contracts can permanently freeze deposited PRC20 funds by starving gas fee deduction in the `isCEA` smart-contract path - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
In the `isCEA` inbound flow, when the recipient is a deployed smart contract (not a UEA), `ExecuteInboundFundsAndPayload` first deposits the PRC20 tokens to the recipient outside any cache scope [1](#0-0) , then attempts `CallExecuteUniversalTx` + `DeductGasFeesFromReceipt` inside a `CacheContext` that is discarded only if fee deduction fails [2](#0-1) . Because the entire `isCEA` branch never sets `shouldRevert` and the code explicitly documents "isCEA failures never create an INBOUND_REVERT outbound" [3](#0-2) , a fee-deduction failure results in a `FAILED` PCTx record with no compensating revert outbound, while the deposit remains permanently committed.

### Finding Description
An unprivileged attacker (acting as the destination-chain sender of an inbound message, or deploying a contract on Push Chain to serve as the CEA recipient) can:
1. Deploy a recipient contract on Push Chain with zero `upc` balance that accepts the `executeUniversalTx` call successfully.
2. Trigger (or wait for) an inbound with `IsCEA=true` pointing at this contract as recipient, with `Amount > 0`.
3. `depositPRC20` mints/credits the PRC20 tokens to the recipient before the cache-scoped call [4](#0-3) .
4. `CallExecuteUniversalTx` succeeds inside the `CacheContext`, but `DeductGasFeesFromReceipt` fails because the recipient has no native `upc` to pay gas [5](#0-4) , so `writeCache()` is never called and the EVM state change is discarded [6](#0-5) .
5. The `callPcTx` is recorded as `FAILED` with `"gas fee deduction failed: ..."` [7](#0-6) , and function returns `nil` without ever calling `buildRevertOutbound` for this branch.

This is exactly reproduced by the repository's own integration test, whose comment references ticket `F-2026-16738` and confirms: deposit stays `SUCCESS`, `callPcTx` is `FAILED` with the fee message, and no `INBOUND_REVERT` outbound is ever created [8](#0-7) .

### Impact Explanation
The deposited PRC20 tokens are permanently locked in the recipient contract: the contract's `executeUniversalTx` call is rolled back (so it cannot use/forward the funds even if it wanted to), the module records the leg as `FAILED`, and the `isCEA` path structurally never emits a revert/refund outbound to return funds to the original sender. This is unauthorized permanent freezing of user-deposited funds with no automated recovery path, directly matching the "Required Impacts" gate for "permanent freezing... of user or protocol-controlled funds."

### Likelihood Explanation
Trivial to trigger: any unprivileged actor can deploy a Push Chain contract with a callable function and zero native balance, then route (or simply wait for) an inbound `IsCEA=true` message that names it as `Recipient`. No privileged role, validator collusion, or special timing is required — only honest validators voting on an inbound whose recipient field the attacker fully controls (the CEA design intentionally lets the source-chain sender specify an arbitrary EVM recipient).

### Recommendation
For the `isSmartContract` sub-path of `isCEA`, when `DeductGasFeesFromReceipt` fails, do not silently swallow the failure as an unrecoverable dead end: either (a) revert the deposit in the same cache scope so deposit and execution commit/fail atomically, or (b) fall back to building a revert/refund outbound (as done in the non-`isCEA` deposit-failure branch) so funds can flow back to `RevertInstructions.FundRecipient` instead of being stranded at a contract that cannot use them.

### Proof of Concept
Reuse the existing test `TestInboundCEASmartContractRecipient/"fee deduction failure rolls back executeUniversalTx, keeps deposit"` [9](#0-8)  and add the assertion (already partially present at lines 476-479) that no `OutboundTx`/`PendingOutbounds` entry of type `INBOUND_REVERT` is created for the UTX, confirming the deposited PRC20 balance (asserted at lines 452-467) has no automated path back to the sender.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L82-100)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L103-103)
```go
		// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L233-256)
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L271-272)
```go
		case feeErr != nil:
			callPcTx.ErrorMsg = fmt.Sprintf("gas fee deduction failed: %s", feeErr.Error())
```

**File:** x/uexecutor/keeper/fees.go (L134-140)
```go
	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
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
