This is exactly the analog to the Bold flash-loan footgun. The `PostTxProcessing` EVM hook (`x/uexecutor/keeper/evm_hooks.go`) fires after **every** EVM transaction, and `CallExecuteUniversalTx`/`CallUEAExecutePayload` call arbitrary attacker-deployed contract code (the CEA recipient or UEA-called target) as a `DerivedEVMCall`. That EVM call produces a real receipt, which the hook scans for `UniversalTxOutbound`/`RescueFundsOnSourceChain` events emitted from the `UNIVERSAL_GATEWAY_PC` address — but the check is purely "did this log come from that fixed address," with no binding to who originated the call or whether it happened inside a to-be-reverted `CacheContext`. [1](#0-0) [2](#0-1) 

### Title
Attacker-controlled CEA recipient contract can forge outbound creation via `UniversalGatewayPC` re-entrant call inside a to-be-discarded `CacheContext` — ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go])

### Summary
`ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` route `isCEA` inbounds whose recipient is a plain smart contract (not a UEA) through `CallExecuteUniversalTx`, which is a `DerivedEVMCall` executing **attacker-deployed bytecode** as the top-level call. [3](#0-2)  This call — and the subsequent `DeductGasFeesFromReceipt` — run inside an `sdkCtx.CacheContext()` that is only committed via `writeCache()` if fee deduction succeeds; otherwise it is silently discarded. [4](#0-3) 

Exactly like the Bold `BalancerFlashLoan.receiver` pattern, this design relies on an implicit assumption that no one else can "consume" or race the state set up mid-flight before the surrounding scope commits or rolls back. Here, the recipient contract is fully attacker-controlled code executing arbitrary EVM calls, including a call to `UniversalGatewayPC` (`0x...C1`) that emits a `UniversalTxOutbound` event. Because `DerivedEVMCall` runs as a real top-level EVM transaction (per `DERIVED_TRANSACTIONS.md`), the EVM keeper's `PostTxProcessing` hook fires for it just like any other tx: [5](#0-4) . That hook calls `CreateUniversalTxFromReceiptIfOutbound`, which builds and commits a brand-new `UniversalTx` + `PendingOutbounds` entry directly off the receipt logs — on the **live** `sdk.Context`, not the module's `CacheContext`. [6](#0-5) 

### Finding Description
The attacker deploys a "recipient" contract for a `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` isCEA inbound. When Universal Validators reach quorum and the keeper calls `CallExecuteUniversalTx` on this contract, the contract's code executes as `msg.sender = uexecutor module account` inside the EVM. Nothing in `RecipientContractABI`/`executeUniversalTx` prevents the recipient from itself calling `UniversalGatewayPC.withdraw`-style entry points that emit `UniversalTxOutbound`, using PRC20 tokens the recipient just received from the deposit (which happened in the *outer*, already-committed context, before the `CacheContext` scope). [7](#0-6) 

Because the EVM's `PostTxProcessing` hook is bound to `DerivedEVMCall`'s own top-level receipt (not gated on whether the surrounding `x/uexecutor` `CacheContext` ultimately commits), the hook's `CreateUniversalTxFromReceiptIfOutbound` writes a new `UniversalTx`/`PendingOutbounds` entry unconditionally against the live store the moment the `executeUniversalTx` call returns — independent of whether `DeductGasFeesFromReceipt` afterward fails and the CacheContext is discarded. The `writeCache()`/discard decision governs only the recipient's own EVM storage and the PRC20/PCTx bookkeeping that `x/uexecutor` explicitly wrote inside the cache; it does **not** roll back state that other EVM-side hooks (like `PostTxProcessing`) already committed to the real context as a side effect of executing inside that same EVM call.

This mirrors the Bold footgun precisely: a piece of transient, call-scoped state (`receiver`/here, "the pending CacheContext that might get thrown away") is trusted implicitly by a component (the hook) that has no reentrancy guard and no knowledge that its caller is inside a revocable scope. An attacker who controls the code executing in that window can trigger side effects that survive regardless of the outer rollback decision.

### Impact Explanation
If confirmed, an attacker-deployed CEA recipient contract could get a real `PendingOutbounds` entry (with a legitimate-looking `UniversalTxOutbound` event carrying attacker-chosen `token`/`amount`/`target`) permanently created and queued for TSS signing and broadcast, even in scenarios where the enclosing PC-side accounting (deposit/fee) is supposed to be discarded on failure. This risks unauthorized-outbound creation and fund release from Push Chain's TSS-controlled vault to an attacker-controlled destination-chain address, which falls squarely under "unauthorized release... of user or protocol-controlled funds" and "forged... outbound... state accepted through user-reachable flows."

### Likelihood Explanation
Reaching the CEA-smart-contract branch requires only that validators vote (honestly) on an inbound whose `Recipient` is an attacker-deployed contract with `isCEA=true` — no privileged access is needed, and Universal Validators are assumed honest per the audit scope. The rest of the trigger (the malicious contract itself calling `UniversalGatewayPC`) is fully within the attacker's control since it is their own deployed bytecode.

### Recommendation
- Ensure `PostTxProcessing`/`CreateUniversalTxFromReceiptIfOutbound` writes are scoped to the same `CacheContext` used for `CallExecuteUniversalTx` + `DeductGasFeesFromReceipt`, so that a fee-deduction failure (or any other rollback trigger) also discards any outbound state created as a side effect of the nested EVM execution.
- Alternatively/additionally, disallow (or explicitly and safely support) recipient-initiated calls into `UniversalGatewayPC` from within `executeUniversalTx` callbacks — e.g., gate `PostTxProcessing` so it only processes logs from `DerivedEVMCall`s that are not module-internal "probe" calls, or defer outbound creation until after the calling keeper function's `CacheContext` decision is known.
- Add integration coverage mirroring the existing `F-2026-16738` "fee deduction failure rolls back executeUniversalTx" test, but specifically asserting that no `PendingOutbounds`/second `UniversalTx` survives when the recipient contract itself triggers a `UniversalTxOutbound` event during a call whose CacheContext is ultimately discarded.

### Proof of Concept
1. Register a `TokenConfig`/`ChainConfig` enabling outbound for some destination chain, and deploy a malicious `recipient` contract implementing `executeUniversalTx(sourceChain, ceaAddress, payload, amount, prc20AssetAddr, txId)` that, upon being called, immediately calls `UniversalGatewayPC`'s withdraw/outbound entry point using the PRC20 balance it just received from the deposit step.
2. Submit an isCEA `FUNDS_AND_PAYLOAD` inbound with `Recipient` = malicious contract address, and craft it (e.g., zero UPC balance on the recipient) so `DeductGasFeesFromReceipt` will fail after `CallExecuteUniversalTx` succeeds — following the same setup as the existing `fee deduction failure rolls back executeUniversalTx, keeps deposit` test in `test/integration/uexecutor/inbound_cea_smart_contract_test.go`. [8](#0-7) 
3. Get 2/3 validators to vote the inbound (honest validators, unprivileged trigger).
4. Assert that despite `callPcTx.Status == "FAILED"` (fee deduction failure, `executeUniversalTx` cache discarded), a `PendingOutbounds` entry and a second `UniversalTx` (created via `CreateUniversalTxFromReceiptIfOutbound`) exist corresponding to the malicious contract's `UniversalTxOutbound` event — confirming the outbound was **not** rolled back with the rest of the cache.

### Citations

**File:** x/uexecutor/keeper/evm_hooks.go (L25-43)
```go
// PostTxProcessing is called by the EVM module after transaction execution.
// It inspects the receipt and creates UniversalTx + Outbound only if
// UniversalTxWithdraw event is detected.
func (h EVMHooks) PostTxProcessing(
	ctx sdk.Context,
	sender common.Address,
	msg core.Message,
	receipt *ethtypes.Receipt,
) error {
	if receipt == nil || len(receipt.Logs) == 0 {
		return nil
	}

	h.k.Logger().Debug("evm hook post-tx processing",
		"tx_hash", receipt.TxHash.Hex(),
		"sender", sender.Hex(),
		"log_count", len(receipt.Logs),
		"gas_used", receipt.GasUsed,
	)
```

**File:** x/uexecutor/keeper/evm_hooks.go (L60-66)
```go
	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}

	// Handle rescue outbounds (RescueFundsOnSourceChain events → attach to original UTX).
	return h.k.AttachRescueOutboundFromReceipt(ctx, protoReceipt, pcTx)
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L59-102)
```go
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

**File:** x/uexecutor/keeper/evm.go (L646-692)
```go
// CallExecuteUniversalTx calls executeUniversalTx on a smart-contract recipient.
// This is used for isCEA inbounds whose recipient is a deployed contract (not a UEA).
func (k Keeper) CallExecuteUniversalTx(
	ctx sdk.Context,
	recipientAddr common.Address,
	sourceChain string,
	ceaAddress []byte,
	payload []byte,
	amount *big.Int,
	prc20AssetAddr common.Address,
	txId [32]byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	recipientABI, err := types.ParseRecipientContractABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse recipient contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		recipientABI,
		ueModuleAccAddress,
		recipientAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"executeUniversalTx",
		sourceChain,
		ceaAddress,
		payload,
		amount,
		prc20AssetAddr,
		txId,
	)
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L157-185)
```go
// CreateUniversalTxFromReceiptIfOutbound
// Creates a UniversalTx ONLY if outbound events exist in the receipt.
// Safe to call from ExecutePayload, EVM hooks
func (k Keeper) CreateUniversalTxFromReceiptIfOutbound(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	pcTx types.PCTx,
) error {
	universalTxKey, err := k.BuildPcUniversalTxKey(ctx, pcTx)
	if err != nil {
		return errors.Wrap(err, "failed to create UniversalTx key")
	}

	outbounds, err := k.BuildOutboundsFromReceipt(ctx, universalTxKey, receipt)
	if err != nil {
		return err
	}

	if len(outbounds) == 0 {
		return nil
	}

	utx, err := k.CreateUniversalTxFromPCTx(ctx, pcTx)
	if err != nil {
		return err
	}

	return k.attachOutboundsToUtx(ctx, utx.Id, outbounds, "")
}
```

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L354-418)
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
```
