## Title
Failed PRC20 deposit on isCEA inbounds causes permanent loss of bridged funds (no revert path) - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
`ExecuteInboundFundsAndPayload` handles `isCEA` (contract-recipient) inbounds differently from regular UEA inbounds. When `depositPRC20` fails for a non-`isCEA` inbound, the code sets `shouldRevert = true` and schedules an `INBOUND_REVERT` outbound so the user's already-locked source-chain funds are sent back. For `isCEA` inbounds, the exact same failure is explicitly excluded from the revert path — the comment states outright: `// isCEA failures never create an INBOUND_REVERT outbound.` This mirrors the `onERC721Received()` bug class: certain otherwise-valid input combinations (here, `IsCEA=true` + a `depositPRC20` failure) cause the already-received asset to be swallowed with no corrective action and no way to recover it.

### Finding Description
`IsCEA` is a field on the `Inbound` message that is populated from data the user supplies on the source-chain gateway call (it flows in unchanged through `ValidateBasic`/`Canonicalize`/`NormalizeForTxType`, which are lenient and never reject it). Once a quorum of honest Universal Validators votes the same real gateway event and the ballot finalizes, `ExecuteInboundFundsAndPayload` is invoked [1](#0-0) :

```go
if utx.InboundTx.IsCEA {
    ...
    if inboundAmount.Sign() > 0 {
        receipt, execErr = k.depositPRC20(...)
        if execErr != nil {
            execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
        }
    }
    ...
    // isCEA failures never create an INBOUND_REVERT outbound.
} else {
    ...
    if execErr == nil && inboundAmount.Sign() > 0 {
        receipt, err = k.depositPRC20(...)
        if err != nil {
            execErr = fmt.Errorf("depositPRC20 failed: %w", err)
            shouldRevert = true
            revertReason = execErr.Error()
        }
    }
}
```

If `execErr != nil`, the function records a `FAILED` `PCTx` and, only `if shouldRevert`, builds and attaches an `INBOUND_REVERT` outbound (see the "If deposit failed, stop here" block) [2](#0-1) . Because `shouldRevert` is never set to `true` on the `isCEA` branch, any legitimate, unprivileged-user-triggerable `depositPRC20` failure (e.g. exceeding a token's configured liquidity cap, an EVM-level revert inside the PRC20 mint, or any other deterministic mint failure that all honest validators independently reproduce) leaves the UTX permanently in a `FAILED` state with the source-chain funds already locked in the gateway/vault and never minted on Push Chain, and never scheduled for return.

This is fully analogous to the report's `onERC721Received()` bug: an asset is received/locked by the protocol, a specific (attacker-reachable, not validator-misbehavior-dependent) condition on the accompanying data causes no protocol action to be taken, and there is no fallback that reverts or returns the asset — it is stuck forever.

### Impact Explanation
This falls squarely within "permanent loss ... of user or protocol-controlled funds" and "unauthorized state transitions in universal execution flows" from the allowed-impact gate. An ordinary user who deposits into the gateway with `IsCEA=true` and an amount that fails to mint on Push Chain (for any deterministic, validator-agreed reason) loses their funds with certainty: the source-chain deposit already succeeded (funds are locked/consumed there), but the corresponding Push Chain mint fails and, unlike the non-CEA path, no `INBOUND_REVERT` outbound is ever created to send the funds back. There is no operator-driven catch-all for this case comparable to what exists for stuck outbounds (`x/uexecutor/README.md` describes manual governance resolution only for outbound-side stuck states, not for this inbound-side isCEA deposit-failure gap).

### Likelihood Explanation
Reachable by any unprivileged user who can construct a normal gateway deposit with `IsCEA=true`. All that's required is a deterministic `depositPRC20` failure that every honest validator reproduces identically (so ballot finalization and execution stay in consensus) — e.g., hitting the token's `LiquidityCap`, or any other on-chain condition the PRC20/handler contract enforces on mint. This doesn't require malicious validators, malicious relayers, or any privileged actor — it is a pure state-machine gap in the `isCEA` execution branch.

### Recommendation
Treat `depositPRC20` failures identically regardless of `IsCEA`: set `shouldRevert = true` (and populate `revertReason`) whenever the deposit fails and `inboundAmount.Sign() > 0`, before falling into the "isCEA failures never create an INBOUND_REVERT outbound" comment's scope. If the design intent is that `isCEA` payload-execution failures (e.g., `executeUniversalTx` failing) should not revert (since the deposit itself may have already succeeded and funds already landed at the recipient), that should be scoped strictly to *post-deposit* payload failures — not to the deposit step itself. The deposit failure case must always produce a revert outbound so locked source-chain funds are returned to the sender.

### Proof of Concept
1. Attacker (any user) initiates a gateway deposit on a source chain with `TxType = FUNDS_AND_PAYLOAD`, `IsCEA = true`, `Recipient` set to a valid-looking 0x address (either an undeployed/EOA-like target or a contract), and an `Amount` that is known to exceed the configured `TokenConfig.LiquidityCap` for that asset (or otherwise deterministically causes the PRC20 mint call to revert).
2. Honest Universal Validators observe the real, finality-confirmed gateway event and vote it via `MsgVoteInbound`; ballot finalizes with 2/3+, since the observation is genuine and identical across all honest nodes.
3. `VoteInbound` → `NormalizeForTxType` succeeds (no decode error) → `ExecuteInbound` → `ExecuteInboundFundsAndPayload` runs the `IsCEA` branch, calls `depositPRC20`, which reverts due to the liquidity cap.
4. `execErr != nil`, a `FAILED` `PCTx` is recorded on the UTX, but because this is the `isCEA` branch, `shouldRevert` stays `false` and no `INBOUND_REVERT` outbound is ever attached.
5. The user's source-chain funds remain locked in the gateway/vault forever; no PRC20 was minted on Push Chain and no outbound will ever return the funds. Existing integration test `test/integration/uexecutor/inbound_solana_test.go` demonstrates the equivalent non-CEA case correctly records a `FAILED` PCTx via missing token config [3](#0-2) , but no test exercises the `isCEA` deposit-failure path to confirm a revert is (not) created — this is consistent with the explicit "isCEA failures never create an INBOUND_REVERT outbound" behavior in the production code.

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

**File:** test/integration/uexecutor/inbound_solana_test.go (L191-206)
```go
	t.Run("solana FUNDS inbound with missing token config records FAILED PCTx", func(t *testing.T) {
		app, ctx, vals, inbound, coreVals := setupSolanaInboundTest(t, 4, uexecutortypes.TxType_FUNDS)

		// Remove token config to trigger failure
		app.UregistryKeeper.RemoveTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)

		voteToQuorum(t, ctx, app, vals, coreVals, inbound)

		// Fetch UTX and check PCTx status
		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := app.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)
		require.NotEmpty(t, utx.PcTx)
		require.Equal(t, "FAILED", utx.PcTx[0].Status)
	})
```
