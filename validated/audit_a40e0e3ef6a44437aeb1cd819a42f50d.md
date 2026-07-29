### Title
Permanent lock of PRC20 funds when an isCEA smart-contract recipient's `executeUniversalTx` call reverts - ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go])

### Summary
When a user submits an inbound with `IsCEA=true` and a `Recipient` that is a deployed smart contract (not a UEA), `ExecuteInboundFundsAndPayload` first mints/deposits the PRC20 representation of the bridged asset directly to that recipient contract, and only afterward calls `executeUniversalTx` on it inside a `CacheContext`. If that call reverts (or fee deduction fails), only the EVM state from the call is rolled back — the earlier PRC20 deposit stays committed. By explicit design, "isCEA failures never create an INBOUND_REVERT outbound," and the RESCUE_FUNDS admin-recovery path is only eligible when the *first* PCTx (the deposit) failed. In this scenario the deposit succeeded and only the second PCTx failed, so the funds are permanently unrecoverable through any code path in the repository.

### Finding Description
In `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, the isCEA branch (`ExecuteInboundFundsAndPayload`, lines 53-102) classifies the `Recipient` into three cases. For the "deployed smart contract" case (`isSmartContract = true`, line 86), it performs:

1. `depositPRC20(...)` to `ueaAddr` (the attacker/user-supplied recipient) — this mints the PRC20 asset to the recipient contract and is recorded as `PcTx[0]`. [1](#0-0) 

2. Later, inside a separate scope guarded only by `isSmartContract` (lines 209-283), `CallExecuteUniversalTx` is invoked on the same recipient inside a `CacheContext`; if the call reverts or `DeductGasFeesFromReceipt` fails, `writeCache()` is never called, so *only* that EVM call's side effects roll back — the deposit made in step 1 stays committed. [2](#0-1) 

3. The code comment explicitly states this class of failure never gets an automatic refund path: "isCEA failures never create an INBOUND_REVERT outbound" (line 103, and mirrored in `execute_inbound_funds.go` and `handle_failed_inbound_validation.go`). [3](#0-2) 

4. The only other salvage mechanism, `AttachRescueOutboundFromReceipt`, explicitly requires that the **first** PCTx (the deposit) have `Status == "FAILED"` for CEA inbounds to be rescue-eligible: [4](#0-3) 

Since the deposit in this scenario succeeded (it is `PcTx[0]` with `Status = "SUCCESS"`) and only the subsequent `executeUniversalTx` call (`PcTx[1]`) failed, the rescue-eligibility check `originalUtx.PcTx[0].Status != "FAILED"` evaluates true and rejects rescue with `"rescue: UTX %s CEA deposit did not fail"`. There is no other retry, revert, or rescue mechanism reachable for this exact state combination anywhere in `x/uexecutor`.

This is the direct Push Chain analog of the XVS bridge issue: `OFTCoreV2._sendAndCallAck()` credits/mints tokens to `_toAddress` and only afterward invokes the callback; if the callback (a required contract-only interaction) fails, the already-minted tokens sit at an address that never got its intended processing logic to run, and no return path exists. Here, PRC20 is deposited to the recipient contract and only afterward `executeUniversalTx` is invoked; if it reverts, the tokens sit at the recipient with no completed business logic and no recovery path (no auto-revert, no eligible rescue).

### Impact Explanation
This causes a permanent freezing of user/protocol-bridged funds (PRC20 tokens minted from real, externally-locked collateral) with no on-chain recovery mechanism — neither the automatic per-inbound revert flow nor the admin-driven rescue flow can retrieve them, because the design explicitly special-cases isCEA failures out of the revert flow, and the rescue flow's eligibility check only covers the case where the *deposit itself* failed, not the case where the deposit succeeded but the downstream contract call failed. This is squarely in scope as "permanent freezing... of user or protocol-controlled funds" reachable via ordinary user-submitted inbound transactions and honest validator/node behavior, with no privileged actor required to trigger it.

### Likelihood Explanation
Any ordinary user can trigger this by depositing to a recipient contract on Push Chain that reverts `executeUniversalTx` (e.g., due to a bug, an access-control check, insufficient gas from `GasLimit`, or any legitimate revert condition in third-party recipient contracts used across the ecosystem). Because `IsCEA`/`Recipient`/payload are attacker/depositor-controlled fields decoded from the source-chain gateway event, and honest validators/nodes will faithfully execute this path exactly as coded, the trigger requires no malicious validator, no privileged action, and no external-chain dishonesty — only a normal deposit routed to a contract whose `executeUniversalTx` implementation reverts for any reason (including reasons outside the depositor's control, e.g. a contract used by many senders that later becomes broken or paused).

### Recommendation
Wrap the deposit and the `executeUniversalTx` call together in the same `CacheContext`/atomic unit so that if the contract call fails, the deposit is rolled back too (mirroring the existing "fee deduction failure rolls back executeUniversalTx" pattern, but extended to cover the deposit as well). Alternatively, extend the rescue-eligibility check in `AttachRescueOutboundFromReceipt` to also treat "deposit succeeded but the subsequent `executeUniversalTx` PCTx failed" as rescue-eligible, and/or allow an `INBOUND_REVERT` outbound to be created for isCEA smart-contract-call failures specifically (as is already done for non-isCEA failures), so bridged value is never left irrecoverably stranded at a recipient whose intended logic never executed.

### Proof of Concept
1. An attacker (or any regular user, or a third party targeting a shared recipient contract) submits a source-chain gateway deposit with `TxType=FUNDS_AND_PAYLOAD`, `IsCEA=true`, and `Recipient` set to the address of a deployed (non-UEA) smart contract whose `executeUniversalTx` implementation will revert (e.g., a contract lacking sufficient balance to cover gas, or one that reverts on unexpected `payload`/`sourceChain` data — see the existing integration test `inbound_cea_smart_contract_test.go` demonstrating the "fee deduction failure" variant of this exact code path).
2. Validators reach quorum and vote the inbound in via `MsgVoteInbound`; `ExecuteInboundFundsAndPayload` runs.
3. `depositPRC20` succeeds, minting the PRC20 to the recipient contract and recording `PcTx[0].Status = "SUCCESS"`. [5](#0-4) 
4. `CallExecuteUniversalTx` reverts (or `DeductGasFeesFromReceipt` fails); the `CacheContext` write is skipped, so no state related to the payload commits, but the deposit from step 3 remains committed. `PcTx[1].Status = "FAILED"` is recorded. [6](#0-5) 
5. No `INBOUND_REVERT` outbound is created (per the isCEA design comment), and any later attempt to invoke the rescue path via `AttachRescueOutboundFromReceipt` fails with `"rescue: UTX %s CEA deposit did not fail"` because `PcTx[0].Status == "SUCCESS"`. [7](#0-6) 
6. The PRC20 tokens minted in step 3 remain at the recipient contract address permanently, with no on-chain mechanism in `x/uexecutor` capable of returning or re-routing them.

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L161-185)
```go
	// --- record deposit attempt (only if amount > 0 or there was an error)
	if inboundAmount.Sign() > 0 || execErr != nil {
		depositPcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
			Status:      "FAILED",
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			depositPcTx.TxHash = receipt.Hash
			depositPcTx.GasUsed = receipt.GasUsed
		}
		if execErr != nil {
			depositPcTx.ErrorMsg = execErr.Error()
		} else {
			depositPcTx.Status = "SUCCESS"
		}
		updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &depositPcTx)
			return nil
		})
		if updateErr != nil {
			return updateErr
		}
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L233-282)
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L239-250)
```go
		// Rescue eligibility differs by inbound type:
		//
		//  CEA inbounds: the deposit (first PCTx) must have failed, meaning the funds
		//  never arrived on Push Chain and are still locked on the source chain.
		//
		//  Non-CEA inbounds: the auto-generated INBOUND_REVERT outbound must exist and
		//  have reached REVERTED status, meaning TSS could not return the funds to the
		//  source chain and they are stuck (held by the gateway contract or in escrow).
		if originalUtx.InboundTx.IsCEA {
			if len(originalUtx.PcTx) == 0 || originalUtx.PcTx[0] == nil || originalUtx.PcTx[0].Status != "FAILED" {
				return fmt.Errorf("rescue: UTX %s CEA deposit did not fail", originalUtxId)
			}
```
