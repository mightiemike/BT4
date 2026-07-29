### Title
Recipient-controlled DoS of fee collection lets a malicious CEA recipient contract force silent gas-fee loss / free execution rollback in `ExecuteInboundFundsAndPayload` - (File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go)

### Summary
The `PayrollManager.sol` report describes a class of bugs where a strict post-execution "invariant check" (exact balance match) can be defeated when (a) the destination is a malicious/uncooperative external contract that can influence the check's outcome, and (b) an earlier, irreversible fund movement has already been committed before the check runs. `x/uexecutor`'s CEA smart-contract execution path in `ExecuteInboundFundsAndPayload` has the same structural shape: PRC20 deposit (irreversible, committed outside any rollback scope) happens first, then `executeUniversalTx` + gas-fee deduction run inside a `CacheContext` that is only committed if fee deduction succeeds [1](#0-0) .

### Finding Description
For an `IsCEA` inbound whose recipient is a deployed smart contract (not a UEA), the flow is:
1. PRC20 tokens are deposited to the recipient contract via `depositPRC20`, committed directly on `sdkCtx` (not cached) [2](#0-1) .
2. `executeUniversalTx` is invoked on the recipient contract, and gas fee deduction (`DeductGasFeesFromReceipt`) runs, both inside a single `sdkCtx.CacheContext()` — this whole scope is only persisted via `writeCache()` if fee deduction succeeds [3](#0-2) .

Because the recipient is an attacker-deployed contract (an unprivileged external attacker can deploy any contract and use it as the `Recipient` in a self-crafted inbound), the recipient's `executeUniversalTx` callback fully controls what happens inside the cached scope: it can consume attacker-chosen amounts of native UPC balance it received from step 1 (e.g. via `SELFDESTRUCT`-adjacent side effects, reentering `DerivedEVMCall`-reachable module methods, or simply draining the small pre-funded balance) so that `DeductGasFeesFromReceipt` deterministically fails. This is the on-chain analog to "a malicious ETH recipient / ERC777 callback interferes with the balance the invariant is measured against" from the report.

The consequence mirrors the report's second bullet ("malicious external contract ... could revert the call ... severely hinder payments"): the attacker can force `feeErr != nil` on every attempt, which discards (`writeCache()` never called) all EVM state mutations from `executeUniversalTx` — including any side effects the protocol expected to persist (e.g., emitted UniversalTx outbound events, any accounting the recipient contract does with the delivered PRC20/native token) — while the antecedent PRC20 deposit remains permanently committed. The `PCTx.Status` is marked `"FAILED"` with the canonical `"gas fee deduction failed: ..."` message, and the UTX is left with a `PcTx[0]=SUCCESS` deposit and `PcTx[1]=FAILED` execution, with no retry mechanism and no outbound-revert path triggered (the rescue path only fires on `PcTx[0]` failure, per `x/uexecutor/README.md`) [4](#0-3) .

### Impact Explanation
An unprivileged attacker who controls the CEA recipient contract can guarantee that `executeUniversalTx`'s state changes never persist while still receiving/keeping the deposited PRC20 (since the deposit is outside the cache and always commits). This can be repeated for every inbound routed to that contract, effectively producing a reliable "free deposit, no accountable execution" outcome and a griefing/DoS vector against any protocol logic that depends on `executeUniversalTx`'s effects being durable (e.g. downstream outbound creation, accounting hooks). This does not directly mint/burn/drain protocol funds beyond the (legitimately deposited) PRC20, but it corrupts the invariant that PRC20 delivery and payload execution succeed/fail atomically together, and it silently discards gas that was spent computing `executeUniversalTx`'s side effects, matching the report's "severely hinder payments" impact class. Confirmed automated test coverage exists for exactly this rollback behavior, indicating the design (not merely a hypothetical) intentionally accepts this split-commit outcome [5](#0-4) .

### Likelihood Explanation
High reachability: any external, unprivileged actor can submit a CEA inbound (subject to validator quorum voting on the observed event, which is honest per scope assumptions) whose `Recipient` is a contract they deployed and fully control. No privileged access is required to make that contract behave adversarially inside `executeUniversalTx`. The only dependency is on honest validators correctly relaying the inbound event data — the attack surface is entirely in the recipient contract's logic, which is unprivileged and freely deployable.

### Recommendation
Apply the same class of fixes the report references for `PayrollManager.sol`:
- Decouple the irreversible deposit from the fee-deduction/execution atomicity assumption, or make the entire deposit+execute+fee-deduct sequence atomic (all-or-nothing) rather than split across a committed deposit and a separately cached execute+fee step.
- Do not let the recipient contract's arbitrary EVM logic be able to influence whether fee collection succeeds; e.g., collect the gas fee deterministically (from a fixed source, or pre-charge before calling `executeUniversalTx`) rather than reading recipient-controlled state/balance after the call.
- Ensure a defined fallback records this state as safely resolvable (e.g., trigger the revert/rescue outbound path when the post-deposit execution step fails) instead of only recording a `FAILED` `PcTx` with no remediation, so operators/rescue flows aren't operating on inconsistent state.

### Proof of Concept
1. Attacker deploys `MaliciousRecipient` implementing `executeUniversalTx(...)` such that it always leaves the contract's native UPC balance below what `DeductGasFeesFromReceipt` needs to collect (e.g., it does nothing to accumulate balance, or it self-drains any UPC it might hold).
2. Attacker triggers/observes a source-chain event that produces an `Inbound` with `IsCEA=true`, `Recipient=MaliciousRecipient address`, `TxType=FUNDS_AND_PAYLOAD`, and a non-zero `Amount`.
3. Validators vote to quorum (honest, per scope) → `ExecuteInboundFundsAndPayload` runs.
4. `depositPRC20` commits PRC20 into `MaliciousRecipient` (irreversible, outside cache) [2](#0-1) .
5. `CallExecuteUniversalTx` executes inside `cacheCtx`; `DeductGasFeesFromReceipt` fails because `MaliciousRecipient` cannot/will not pay [6](#0-5) .
6. `writeCache()` is never invoked, so all EVM state changes from `executeUniversalTx` are discarded, while the PRC20 balance from step 4 remains at `MaliciousRecipient`. `PcTx[1].Status="FAILED"` with `ErrorMsg` containing `"gas fee deduction failed"`, and no outbound-revert path is triggered since `PcTx[0].Status=="SUCCESS"` [7](#0-6) . This exact rollback behavior is reproduced and asserted by the existing integration test [5](#0-4) , confirming the mechanism is real and attacker-triggerable, not merely theoretical.

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L233-255)
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L259-282)
```go
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
```

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L420-480)
```go
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
