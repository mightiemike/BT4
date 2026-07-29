## Analog Found

### Title
Malicious CEA recipient can drain module-account gas during `executeUniversalTx` calls, permanently stalling inbound finalization — (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
The external report describes a class of bug where a message-processing flow forwards attacker-controlled gas into an untrusted external call, and the caller has no reserved gas budget to safely persist failure state or continue post-call bookkeeping — letting an attacker deploy a gas-draining contract to permanently block the message pathway. Push Chain's `x/uexecutor` module has a structurally similar pattern: `ExecuteInboundFundsAndPayload` lets an inbound whose `Recipient` is *any deployed contract* (not necessarily a UEA) be invoked via `CallExecuteUniversalTx`, which issues a `DerivedEVMCall` with `gasLimit = nil` to attacker-controlled bytecode, with no bounded reserve for the surrounding module bookkeeping.

### Finding Description
In `ExecuteInboundFundsAndPayload` [1](#0-0) , when an `IsCEA` inbound's `Recipient` is not a recognized UEA but has contract code, the module sets `isSmartContract = true` and later calls `CallExecuteUniversalTx` on that address [2](#0-1) . That function issues a `DerivedEVMCall` as the module account, and critically passes `gasLimit = nil` [3](#0-2) , unlike the user-payload path (`CallUEAExecutePayload`), which enforces an explicit, user-supplied `gasLimit` from the `UniversalPayload` [4](#0-3) .

The `gasLimit=nil` semantics are documented as "use a sensible default" [5](#0-4) , but that default is defined only in the external, pinned EVM fork (`github.com/pushchain/evm`), which is not part of this repository's index and could not be inspected here. Since the `Recipient` for this code path is any bytecode-bearing address that an unprivileged user can deploy on the destination side and specify as the CEA target of their own inbound, the attacker fully controls the code executed by the module account with essentially the module's default/maximum allowed gas — mirroring the "malicious recipient with unbounded gas-draining logic" primitive from the original report.

The call happens inside a `CacheContext` derived from the same `sdkCtx` [6](#0-5) ; `CacheContext()` branches the multistore but shares the underlying `GasMeter`, so EVM gas consumed by the attacker's contract is ultimately charged against the same gas meter driving the surrounding Cosmos message execution (the `MsgVoteInbound` handler that triggers ballot finalization and this execution path). If a malicious CEA contract consumes gas up to whatever the fork's default cap is, there is no reserved gas budget analogous to the report's recommended `gasAllocation` for the subsequent bookkeeping steps — `DeductGasFeesFromReceipt`, `UpdateUniversalTx`, and the two nested `k.UpdateUniversalTx` writer calls that record the outcome [7](#0-6) .

### Impact Explanation
If the module-account gas meter for the finalizing `MsgVoteInbound` transaction is exhausted mid-flight by the malicious CEA call, the panic/out-of-gas condition propagates and causes the entire finalizing transaction (including the vote tally write and any earlier state updates staged in the same tx) to abort, since Cosmos SDK's `runTx` recovers panics by discarding all uncommitted writes for that tx. Because this is deterministic given the same inputs, every honest validator whose vote would cross quorum for that specific inbound hits the identical failure, so the ballot for that inbound can never be finalized through the normal path — the "pathway" (finalization of that particular inbound/UniversalTx) is durably blocked, similar in effect to the LayerZero "blocked channel" outcome, though scoped to the specific malicious inbound/CEA rather than the whole cross-chain channel.

### Likelihood Explanation
Triggering this requires only: (1) deploying an ordinary contract with an expensive fallback/`executeUniversalTx`-invoked loop on any supported destination chain, and (2) submitting a normal inbound naming that contract as `Recipient` with `IsCEA=true` and no prior UEA registration at that address — both are actions available to any unprivileged external user, matching the "unprivileged external attacker" scope. The main uncertainty is the exact default `gasLimit` value and the EVM fork's precise gas-accounting bridge between `DerivedEVMCall`'s internal EVM execution and the outer SDK `GasMeter`, since that logic lives in the external `github.com/pushchain/evm` fork rather than in this repository's indexed code.

### Recommendation
For module-originated calls into attacker-influenced contract addresses (the `CallExecuteUniversalTx` CEA path), pass an explicit, capped `gasLimit` to `DerivedEVMCall` instead of `nil`, sized so that a full consumption of that cap still leaves enough of the outer transaction's gas budget for `DeductGasFeesFromReceipt` and the `UpdateUniversalTx` bookkeeping calls to complete and persist a `FAILED` status deterministically, rather than allowing an out-of-gas condition to abort the entire finalizing transaction. This mirrors the report's recommendation of reserving a fixed `gasAllocation` for post-call state persistence.

### Proof of Concept
1. On a source chain, deploy `GasDrain` with a fallback/entry function matching whatever selector `executeUniversalTx` dispatches to on the recipient side, containing a large bounded loop (e.g., writing to thousands of storage slots) sized to consume most of the default gas budget used by `CallExecuteUniversalTx`.
2. Submit (or have relayed) an inbound event with `IsCEA=true`, `Recipient=address(GasDrain)`, and a small nonzero `Amount`/`AssetAddr`, so that `ExecuteInboundFundsAndPayload` marks `isSmartContract=true` for this recipient [1](#0-0) .
3. Once validators vote and the ballot crosses quorum, the finalizing `MsgVoteInbound` handler calls `ExecuteInboundFundsAndPayload`, which calls `CallExecuteUniversalTx` with `gasLimit=nil` against `GasDrain` [8](#0-7) .
4. Observe (in a devnet/testnet build that vendors the actual `github.com/pushchain/evm` fork) whether the resulting gas consumption exhausts the outer SDK gas meter for the finalizing transaction, causing the whole transaction — and thus the ballot finalization for that inbound — to fail repeatedly for every voting validator.

Note: full confirmation of the exact gas-forwarding/accounting behavior requires inspecting `DerivedEVMCall`'s implementation in the external `github.com/pushchain/evm` fork, which is outside this repository's indexed contents; a Devin session with full repository/dependency access would be needed to validate the precise default gas cap and SDK-gas-meter interaction.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L81-101)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L239-256)
```go
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

**File:** x/uexecutor/keeper/evm.go (L172-192)
```go
	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
```

**File:** x/uexecutor/keeper/evm.go (L673-692)
```go
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

**File:** DERIVED_TRANSACTIONS.md (L59-59)
```markdown
| `gasLimit` | Explicit gas limit (`nil` -> use a sensible default). Critical for predictable receipts. |
```
