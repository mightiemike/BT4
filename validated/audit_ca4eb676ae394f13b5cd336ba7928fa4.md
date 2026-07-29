### Title
`ExecuteInbound` dispatch switch fails to handle `TxType_PAYLOAD`, `TxType_INBOUND_REVERT`, and `TxType_RESCUE_FUNDS`, causing ballot-finalized inbounds to hard-fail at final execution - (File: `x/uexecutor/keeper/execute_inbound.go`)

### Summary
The external report's root cause is a dispatcher (`ArbitrumCoreBranchRouter.executeNoSettlement`) that was not updated when a new value (`0x07`) was added to a function-selector enum that a sibling dispatcher (`CoreBranchRouter.executeNoSettlement`) already handled. The same "enum grew, one dispatcher didn't" pattern exists in Push Chain's `x/uexecutor` module for `TxType`.

### Finding Description
`TxType` is a 7-value enum: `GAS`, `GAS_AND_PAYLOAD`, `FUNDS`, `FUNDS_AND_PAYLOAD`, `PAYLOAD`, `INBOUND_REVERT`, `RESCUE_FUNDS`. [1](#0-0) 

Several code paths already know about and produce/normalize all seven values:
- `SolidityTxTypeToProto` converts the raw `uint8 TX_TYPE` field emitted by the source-chain gateway event into `TxType_RESCUE_FUNDS` and `TxType_INBOUND_REVERT` (in addition to the four "basic" types), so these are legitimate values an honest Universal Validator will decode straight from an attacker-triggered source-chain event and relay via `MsgVoteInbound`. [2](#0-1) 
- `Inbound.NormalizeForTxType()` explicitly documents `PAYLOAD` as valid inbound semantics ("Pure payload execution, no value movement") in the module README's `TxType` table, alongside `INBOUND_REVERT` ("Reverts a previously-executed inbound...") and `RESCUE_FUNDS` ("Admin-driven rescue path for stuck funds") — all listed as having defined *Inbound* semantics. [3](#0-2) [4](#0-3) 

But the actual inbound-execution dispatcher only handles four of the seven values:
```go
switch utx.InboundTx.TxType {
case types.TxType_GAS: ...
case types.TxType_FUNDS: ...
case types.TxType_FUNDS_AND_PAYLOAD: ...
case types.TxType_GAS_AND_PAYLOAD: ...
default:
    return fmt.Errorf("unsupported inbound tx type: %d", utx.InboundTx.TxType)
}
``` [5](#0-4) 

`PAYLOAD`, `INBOUND_REVERT`, and `RESCUE_FUNDS` all fall through to the `default` branch and return an error.

Tracing the call site, `VoteInbound` creates the canonical `UniversalTx` record and removes the `PendingInbounds` entry *before* calling `ExecuteInbound` at the final step:
```go
if err := k.CreateUniversalTx(ctx, universalTxKey, utx); err != nil { return err }
...
if err := k.RemovePendingInbound(ctx, inbound); err != nil { return err }
...
if validationErr := inbound.ValidateForExecution(); validationErr != nil { ...handled path... }
...
if err := k.ExecuteInbound(ctx, utx); err != nil {
    return err
}
``` [6](#0-5) 

Because these writes happen directly against the (un-cached) `ctx`/`sdkCtx` rather than a `CacheContext`, and `VoteInbound` propagates the `ExecuteInbound` error straight up to the `MsgServer` handler, the entire finalizing `MsgVoteInbound` transaction fails at the Cosmos SDK / baseapp level, rolling back the state written during that message — including the ballot-finalization vote that was flushed by the earlier `commit()` call.

### Impact Explanation
If a source-chain gateway event carries `TX_TYPE` values that map to `RESCUE_FUNDS`/`INBOUND_REVERT` (or an inbound is otherwise constructed with `TxType_PAYLOAD`), the finalizing vote transaction will always fail at the final `ExecuteInbound` step and be reverted in its entirety. Because the failure happens **after** the finalizing vote is cast but the whole transaction — including the vote itself — is rolled back on error, the ballot can never actually reach a terminal `PASSED` state through this path: every attempt by an honest UV to cast the deciding vote fails and is undone, so the inbound is stuck in perpetual re-voting with no forward progress. If real value was deposited/locked in the source-chain gateway/vault as part of that event, this results in **permanent freezing of user funds**, since Push Chain can never successfully finalize and execute (or explicitly revert) the corresponding `UniversalTx`.

### Likelihood Explanation
Reachability depends on whether the external gateway contract on the source chain permits an unprivileged caller to emit an event with `TX_TYPE=4` (`RESCUE_FUNDS`) or `TX_TYPE=5` (`INBOUND_REVERT`) as decoded by `SolidityTxTypeToProto`. That contract is out of this repository's scope, so I cannot directly confirm the on-chain authorization of the source-chain gateway from this codebase alone. What is confirmed and in-scope is that **Push Chain's own `x/uexecutor` code has no defense-in-depth check** rejecting these `TxType` values earlier in the pipeline (`ValidateBasic`/`ValidateForExecution` were not confirmed to reject them, and `NormalizeForTxType`'s `default` branch treats them as ordinary, valid, non-payload inbound types) before they reach the unhandled `default` case in `ExecuteInbound`. This mirrors exactly the reported bug class: an enum member is valid and reachable through upstream logic, but a specific dispatcher was never updated to route it.

### Recommendation
- Add explicit `case` handling (or an explicit, intentional rejection with a documented invariant) for `TxType_PAYLOAD`, `TxType_INBOUND_REVERT`, and `TxType_RESCUE_FUNDS` in `ExecuteInbound` (`x/uexecutor/keeper/execute_inbound.go`).
- If any of these types are never supposed to reach `ExecuteInbound` as an *inbound* dispatch (e.g., they are outbound-only or internally-synthesized), enforce that invariant earlier — in `Inbound.ValidateBasic()` or `ValidateForExecution()` — so the ballot fails fast and cleanly (with the existing `handleFailedInboundValidation` / revert-scheduling path) instead of failing deep inside the finalized-vote transaction and rolling back the vote itself.
- Ensure the finalization + execution sequence in `VoteInbound` cannot leave a ballot permanently unable to finalize: any known-unexecutable `TxType` should be caught and diverted to the revert/failure path *before* committing the deciding vote, not after.

### Proof of Concept
1. An external actor calls the source-chain gateway contract in a way that emits an event with `TX_TYPE = 4` (or `5`), if the gateway contract does not adequately restrict this field. This decodes via `SolidityTxTypeToProto` to `TxType_RESCUE_FUNDS` (or `TxType_INBOUND_REVERT`). [2](#0-1) 
2. Honest Universal Validators observe the event and submit `MsgVoteInbound` with this `Inbound.TxType`.
3. On the deciding (threshold-reaching) vote, `VoteInbound` creates the `UniversalTx`, removes the `PendingInbounds` entry, passes `NormalizeForTxType`/`ValidateForExecution`, and calls `ExecuteInbound`. [6](#0-5) 
4. `ExecuteInbound` falls into its `default` case and returns `"unsupported inbound tx type"`. [5](#0-4) 
5. The error propagates up, the entire `MsgVoteInbound` transaction (including the just-committed deciding vote) is reverted by the SDK. The inbound never finalizes and the corresponding source-chain funds (if any) can never be released or explicitly reverted through this flow.

**Note on confidence**: I could not verify from the indexed code whether `Inbound.ValidateForExecution()` (referenced in `msg_vote_inbound.go` but whose body I did not retrieve) already rejects these `TxType` values earlier, nor whether the external Solidity gateway contract restricts who can set `TX_TYPE=4/5`. Both would materially affect whether this is exploitable by a fully unprivileged actor versus only reachable via a benign/expected internal code path. I recommend a Devin session with full repository and gateway-contract access to confirm `ValidateForExecution()`'s behavior for these three `TxType` values and to check the source-chain gateway's authorization on `TX_TYPE`.

### Citations

**File:** proto/uexecutor/v1/types.proto (L84-93)
```text
enum TxType {
  UNSPECIFIED_TX    = 0;
  GAS               = 1;
  GAS_AND_PAYLOAD   = 2;
  FUNDS             = 3;
  FUNDS_AND_PAYLOAD = 4;
  PAYLOAD           = 5;
  INBOUND_REVERT    = 6;
  RESCUE_FUNDS      = 7;
}
```

**File:** x/uexecutor/types/tx_type.go (L1-21)
```go
package types

// Solidity TX_TYPE (uint8) → Cosmos TxType
func SolidityTxTypeToProto(txTypeUint8 uint8) TxType {
	switch txTypeUint8 {
	case 0:
		return TxType_GAS
	case 1:
		return TxType_GAS_AND_PAYLOAD
	case 2:
		return TxType_FUNDS
	case 3:
		return TxType_FUNDS_AND_PAYLOAD
	case 4:
		return TxType_RESCUE_FUNDS
	case 5:
		return TxType_INBOUND_REVERT
	default:
		return TxType_UNSPECIFIED_TX
	}
}
```

**File:** x/uexecutor/README.md (L124-136)
```markdown
### `TxType` — what flavour of crosschain action

The same enum is used on both `Inbound` and `OutboundTx` to describe what the message is for.

| `TxType` | Inbound semantics | Outbound semantics |
|---|---|---|
| `GAS` | User pre-paid gas on the source chain. Mints PC to the recipient as a gas top-up. | Refund of unused gas back to a source chain. |
| `GAS_AND_PAYLOAD` | Gas top-up + executes a payload through the recipient's UEA in the same Push Chain tx. | Same combo on the destination side. |
| `FUNDS` | Pure synthetic transfer — mints PRC20 representation of an external token. | Pure transfer of a PRC20 back out of Push Chain. |
| `FUNDS_AND_PAYLOAD` | Mints funds + runs a payload (e.g. deposit + DEX swap atomically). | Funds delivery with a destination-side call. |
| `PAYLOAD` | Pure payload execution, no value movement. | Pure call on the destination chain. |
| `INBOUND_REVERT` | Reverts a previously-executed inbound (returns funds to the source-chain sender). | — |
| `RESCUE_FUNDS` | Admin-driven rescue path for stuck funds. | Outbound that delivers the rescue. |
```

**File:** x/uexecutor/types/inbound.go (L42-72)
```go
func (p *Inbound) NormalizeForTxType() error {
	switch p.TxType {
	case TxType_FUNDS_AND_PAYLOAD, TxType_GAS_AND_PAYLOAD:
		// Payload types: recipient is only meaningful when isCEA
		if !p.IsCEA {
			p.Recipient = EvmZeroAddress
		}
		// Always clear universal_payload — whatever the UV sends is ignored.
		// Core validator decodes from raw_payload.
		p.UniversalPayload = nil

		// Decode raw_payload → universal_payload
		if p.RawPayload != "" {
			decoded, err := DecodeRawPayload(p.RawPayload, p.SourceChain)
			if err != nil {
				return fmt.Errorf("failed to decode raw payload: %w", err)
			}
			if decoded == nil {
				return fmt.Errorf("raw_payload decoded to nil for payload tx type")
			}
			p.UniversalPayload = decoded
			p.RawPayload = "" // clear after successful decode to save storage
		}
	default:
		// Non-payload types: payload is not used
		p.UniversalPayload = nil
		p.VerificationData = ""
		p.RawPayload = ""
	}
	return nil
}
```

**File:** x/uexecutor/keeper/execute_inbound.go (L18-34)
```go
	switch utx.InboundTx.TxType {
	case types.TxType_GAS: // fee abstraction
		return k.ExecuteInboundGas(ctx, *utx.InboundTx)

	case types.TxType_FUNDS: // synthetic
		return k.ExecuteInboundFunds(ctx, utx)

	case types.TxType_FUNDS_AND_PAYLOAD: // synthetic + payload
		return k.ExecuteInboundFundsAndPayload(ctx, utx)

	case types.TxType_GAS_AND_PAYLOAD: // fee abstraction + payload
		return k.ExecuteInboundGasAndPayload(ctx, utx)

	default:
		k.Logger().Error("unsupported inbound tx type", "utx_key", utx.Id, "tx_type", utx.InboundTx.TxType)
		return fmt.Errorf("unsupported inbound tx type: %d", utx.InboundTx.TxType)
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L109-157)
```go
	utx := types.UniversalTx{
		Id:         universalTxKey,
		InboundTx:  &inbound,
		PcTx:       nil,
		OutboundTx: nil,
	}

	// Step 5: Create the UniversalTx — this must succeed for any further processing
	if err := k.CreateUniversalTx(ctx, universalTxKey, utx); err != nil {
		return err
	}

	k.Logger().Info("utx created",
		"utx_key", universalTxKey,
		"source_chain", inbound.SourceChain,
		"tx_type", inbound.TxType.String(),
		"amount", inbound.Amount,
	)

	// Step 6: Remove from pending inbound set
	if err := k.RemovePendingInbound(ctx, inbound); err != nil {
		return err
	}

	// Step 7: Validate execution prerequisites.
	// If validation fails, record a failed PCTx and schedule revert (for non-isCEA)
	// instead of failing the vote — so the UTX is always visible on-chain.
	if validationErr := inbound.ValidateForExecution(); validationErr != nil {
		k.Logger().Warn("inbound validation failed, scheduling revert",
			"utx_key", universalTxKey,
			"error", validationErr.Error(),
			"is_cea", inbound.IsCEA,
		)
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, validationErr); handleErr != nil {
			return handleErr
		}
		return nil
	}

	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
	}

	return nil
```
