### Title
Failed CEA deposit/autoswap on inbound execution never creates a revert outbound, permanently stranding the user's source-chain funds - ([File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go])

### Summary
When an inbound is submitted with `IsCEA = true` (a user-controlled flag on `FUNDS`, `FUNDS_AND_PAYLOAD`, or `GAS_AND_PAYLOAD` inbounds), `ExecuteInboundGasAndPayload` explicitly skips revert-outbound creation on execution failure: `// isCEA failures never create an INBOUND_REVERT outbound.` [1](#0-0)  If the deposit/autoswap call fails for this path, the code records a `FAILED` `PCTx` and returns, with no path to reclaim the user's locked source-chain funds. [2](#0-1)  This is a direct analog of the Linea bug: once the "second transaction" (destination-side execution) fails, the user has no cancel/refund path.

### Finding Description
Non-`isCEA` inbounds correctly set `shouldRevert = true` on token-config, amount, factory-lookup, deploy, or deposit failures, and `buildRevertOutbound` + `attachOutboundsToUtx` schedule a `INBOUND_REVERT` outbound so the bridged funds are refunded on the source chain: [3](#0-2) 

For the `isCEA` branch, the same class of failures (invalid recipient hex, `CallFactoryGetOriginForUEA` error, or `gasAndPayloadDepositAutoSwap` error) sets `execErr` but never sets `shouldRevert`: [4](#0-3)  and the dedicated guard at the bottom intentionally short-circuits before any revert path is built: [2](#0-1) 

The inbound has already been finalized by ballot (2/3+ UV quorum reached, `UniversalTx` created, `PendingInbounds` entry removed) before `ExecuteInboundGasAndPayload` runs, so this is a single, one-shot execution attempt, not a retryable claim like Linea's `claimMessage`. The only administrative recovery path, `RevertStuckInbound`, requires the ballot itself to be in `BallotStatus_BALLOT_STATUS_EXPIRED` [5](#0-4) , but in this scenario the ballot already `PASSED` (finalized) — it is the *execution*, not the *voting*, that failed, so `RevertStuckInbound`'s precondition can never be met and no admin escape hatch exists either.

Because `IsCEA` is a field on `Inbound` that ultimately derives from the source-chain gateway event payload the user submits (decoded via `NormalizeForTxType`/`DecodeRawPayload`), an ordinary unprivileged user can construct an inbound that sets `IsCEA = true` and deliberately (or accidentally) points at conditions that make `gasAndPayloadDepositAutoSwap` fail — e.g. a token whose `GetDefaultFeeTierForToken` / `GetSwapQuote` calls revert, or a `Recipient` that fails the `CallFactoryGetOriginForUEA` check — guaranteeing the deposit-and-swap leg reverts every time.

### Impact Explanation
Once quorum is reached and the ballot is `PASSED`, the source-chain funds are already considered "spent" by the protocol (the vault/gateway transaction has succeeded on the source chain and validators have finalized the inbound). If the `isCEA` deposit/autoswap subsequently fails, the destination-side mint never happens and no revert outbound is ever created, so the user's bridged funds are permanently lost with no user-initiated or admin-initiated recovery path — matching the "permanent loss of bridged funds" impact class.

### Likelihood Explanation
Triggering this requires only a normal, unprivileged inbound submission with `IsCEA = true` (allowed for `FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS_AND_PAYLOAD`) combined with conditions that make the deposit/autoswap call fail — no privileged access, malicious validator, or key compromise is needed. UVs act honestly and vote the true observation; the bug is purely in the deterministic execution-side handling once the ballot passes.

### Recommendation
Treat isCEA deposit/autoswap failures the same as the non-isCEA path: set `shouldRevert = true` (or a dedicated flag) whenever the deposit/autoswap itself fails (i.e., before any state was successfully minted to the recipient), and call `buildRevertOutbound` + `attachOutboundsToUtx` to schedule an `INBOUND_REVERT` outbound. Only skip reverting for isCEA when the failure occurs strictly *after* a successful deposit (e.g., the downstream `executeUniversalTx` call to the CEA contract fails) since in that case funds are already correctly delivered to the recipient's address.

### Proof of Concept
1. Submit a source-chain gateway event that produces an `Inbound` with `TxType = TxType_FUNDS_AND_PAYLOAD` (or `GAS_AND_PAYLOAD`), `IsCEA = true`, a valid hex `Recipient`, and an `AssetAddr`/amount combination chosen so that `gasAndPayloadDepositAutoSwap`'s `GetDefaultFeeTierForToken` or `GetSwapQuote` call fails (e.g., a token without a configured swap pool, or a swap quote that reverts).
2. UVs (honest) observe and vote `MsgVoteInbound` to quorum; the ballot reaches `PASSED`, the `UniversalTx` is created, and `PendingInbounds` entry is removed.
3. `ExecuteInboundGasAndPayload` runs, hits the `isCEA` branch, `gasAndPayloadDepositAutoSwap` returns an error, `execErr != nil`, and execution reaches the `execErr != nil && utx.InboundTx.IsCEA` guard, returning `nil` with only a `FAILED` `PCTx` recorded — no revert outbound is attached. [2](#0-1) 
4. `RevertStuckInbound` cannot help because the ballot status is `PASSED`, not `EXPIRED`. [5](#0-4) 
5. The user's original source-chain funds (already consumed by the gateway) are permanently unrecoverable.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L61-100)
```go
			if utx.InboundTx.IsCEA {
				// isCEA path: recipient is explicitly specified.
				// Three-way check:
				//   1. Recipient is a UEA  → deposit + autoswap + ExecutePayloadV2
				//   2. Recipient is a deployed smart contract (not UEA) → deposit + autoswap + executeUniversalTx
				//   3. Neither → record FAILED PCTx, no INBOUND_REVERT
				if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
					execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
				} else {
					ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)

					_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
					if ueaCheckErr != nil {
						execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
					} else if isUEA {
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

**File:** x/uexecutor/keeper/admin_revert.go (L47-51)
```go
	if ballot.Status != uvalidatortypes.BallotStatus_BALLOT_STATUS_EXPIRED {
		return "", "", errors.Wrap(sdkErrors.ErrInvalidRequest,
			fmt.Sprintf("ballot %s status is %s; admin revert requires EXPIRED (use MsgRecomputeBallotQuorum to drive a stuck pending ballot to EXPIRED)",
				ballotKey, ballot.Status.String()))
	}
```
