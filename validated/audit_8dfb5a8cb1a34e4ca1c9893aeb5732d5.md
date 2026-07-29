### Title
Permanent freezing of bridged funds when an `isCEA` inbound targets a non-conforming smart-contract recipient - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
The `ExecuteInboundFundsAndPayload` flow for `IsCEA` inbounds unconditionally deposits (mints) PRC20 tokens to a recipient contract *before* verifying that the contract actually implements the `executeUniversalTx` interface it is about to be called with. Detection of "is this a smart contract" only checks that the address has non-empty bytecode — exactly the same class of mistake as the Synthetix report, which assumed a deployed contract at a known address implements a specific interface (`mint()`) without verifying it, causing an irrecoverable revert/lock in a fund-movement path.

### Finding Description
For `IsCEA` inbounds where the recipient is not a recognized UEA, the keeper classifies the recipient purely by codehash: [1](#0-0) 

If the recipient has any bytecode, `isSmartContract` is set true and the PRC20 deposit is executed immediately and committed to the main `sdkCtx` (not a cache context): [2](#0-1) 

Only afterward does the code attempt to call `executeUniversalTx` on that same contract, and this call *is* wrapped in a `CacheContext` that is discarded on failure: [3](#0-2) 

The comment even documents this asymmetry: "the deposit (above this scope) stays committed regardless." Since there is no on-chain check (e.g., ERC-165 or a required marker) that the recipient contract actually implements `executeUniversalTx`, calling it on any arbitrary contract that doesn't support the method reverts — leaving `contractErr != nil`, a `FAILED` PCTx recorded, and the function returns without any recovery action: [4](#0-3) 

Critically, `IsCEA` inbounds are explicitly excluded from the automatic `INBOUND_REVERT` outbound path — the comment at the top of the function states this design intent: [5](#0-4) 

The only manual-recovery mechanism, `AttachRescueOutboundFromReceipt`, requires the *first* PCTx (the deposit) to have `FAILED` status for CEA inbounds: [6](#0-5) 

In this scenario the deposit PCTx succeeds (`SUCCESS`), so rescue eligibility is never met — the tokens are permanently locked in the recipient contract's PRC20 balance with no path to reclaim them.

### Impact Explanation
Any unprivileged user can craft or trigger an inbound (`MsgVoteInbound` observed by honest validators from a genuine source-chain transaction) with `IsCEA = true` and `Recipient` set to any deployed contract address on Push Chain that has bytecode but does not implement `executeUniversalTx` (e.g., a plain ERC20/PRC20 contract, a multisig, or any unrelated dApp contract). The PRC20 representation of the bridged asset is unconditionally minted to that address, the downstream call reverts, no revert-outbound is created (by design for CEA), and the rescue path's precondition (`PcTx[0].Status == "FAILED"`) is unmet since the deposit itself succeeded. This results in permanent, unrecoverable freezing of user-bridged funds — squarely within the "permanent freezing of user or protocol-controlled funds" impact category, reachable purely through ordinary/default inbound submission with honest validators.

### Likelihood Explanation
This requires no privileged access, no malicious validator, and no protocol misconfiguration — only an ordinary cross-chain deposit whose `Recipient`/`IsCEA` fields point at a contract lacking the expected interface. Because CEA is a first-class, user-reachable transaction type in universal execution (the report's "unauthorized module-originated EVM execution" / "universal execution flows" surface), and the codehash check only verifies "has bytecode" rather than "supports this interface," the likelihood of triggering the loss is high — it can happen accidentally (user error routing to the wrong contract) or be deliberately induced by an attacker directing another user's or their own bridged value to a contract they know will revert, exploiting the asymmetric commit/no-revert design.

### Recommendation
Do not commit the PRC20 deposit to the recipient contract before confirming it can process the call. Wrap the deposit and the `executeUniversalTx` call in the same `CacheContext` so both commit or both roll back atomically, and only `writeCache()` after `executeUniversalTx` succeeds (mirroring what is already done for `feeErr`). Alternatively/additionally, verify interface support (e.g. ERC-165, or a lightweight static call probe) before committing funds, and extend rescue eligibility for CEA inbounds to also cover the case where the deposit succeeded but the subsequent `executeUniversalTx` call failed, so an operator-driven recovery path exists.

### Proof of Concept
1. An external-chain user (or an attacker crafting an inbound) sends funds with `IsCEA = true` and `Recipient` = address of any deployed Push-Chain contract that has bytecode but does not implement `executeUniversalTx` (e.g. a plain PRC20 token contract or an unrelated dApp).
2. Validators reach quorum on `MsgVoteInbound`; `ExecuteInboundFundsAndPayload` runs.
3. `codeHash != EmptyCodeHash` → `isSmartContract = true`; `k.depositPRC20(...)` executes and commits directly against `sdkCtx`, minting PRC20 to `Recipient`.
4. `k.CallExecuteUniversalTx(cacheCtx, ...)` reverts because `Recipient` has no matching function selector; `writeCache()` is never invoked, so only the EVM side-effects of the call are discarded — the earlier deposit remains committed.
5. A `FAILED` PCTx is appended; per the isCEA design, no `INBOUND_REVERT` outbound is created.
6. `AttachRescueOutboundFromReceipt`'s CEA rescue check requires `PcTx[0].Status == "FAILED"`, but the deposit PCTx status is `SUCCESS`, so rescue is rejected — the bridged tokens are permanently stranded at `Recipient` with no recovery path.

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L259-283)
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
	}
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
