Confirmed: I've found the concrete gap. `AttachRescueOutboundFromReceipt` in `x/uexecutor/keeper/create_outbound.go` only recognizes rescue-eligibility for two cases — `IsCEA` with a `FAILED` first `PcTx` (deposit never landed), or non-CEA with a `Status_REVERTED` `TxType_INBOUND_REVERT` outbound. It has no branch that recognizes an `ABORTED` `FUNDS` / `GAS_AND_PAYLOAD` / `FUNDS_AND_PAYLOAD` outbound produced by `handleFailedOutbound`'s re-mint failure path in `x/uexecutor/keeper/outbound.go`.

### Title
Permanent fund freeze on outbound re-mint failure — no rescue path for ABORTED FUNDS/GAS_AND_PAYLOAD/FUNDS_AND_PAYLOAD outbounds - (File: x/uexecutor/keeper/outbound.go)

### Summary
When Universal Validators (UVs) honestly report that an outbound (a withdrawal to an external chain) failed, `handleFailedOutbound` tries to compensate the user by re-minting the PRC20 back on Push Chain via `CallPRC20Deposit`. If that re-mint call itself fails, the outbound is marked `ABORTED` (`x/uexecutor/keeper/outbound.go:130-137`) and the function returns — there is no automatic retry and, critically, `AttachRescueOutboundFromReceipt` (`x/uexecutor/keeper/create_outbound.go:239-262`) has no eligibility branch for this case, so the admin-driven `RESCUE_FUNDS` mechanism can never be attached to it. This mirrors the reported bug class: an "emergency"/failure branch results in a balance state that a downstream `require`/eligibility-gate does not recognize, and there is no fallback rescue for the affected value.

### Finding Description
`FinalizeOutbound` → `handleFailedOutbound` (`x/uexecutor/keeper/outbound.go:99-161`) is reached whenever UVs vote `MsgVoteOutbound` with `success=false` for a `TxType_FUNDS`, `TxType_GAS_AND_PAYLOAD`, or `TxType_FUNDS_AND_PAYLOAD` outbound. This is a fully honest, unprivileged-triggerable path: a normal user's own withdrawal (via `MsgExecutePayload` → Gateway `withdraw`/`withdrawAndExecute`) can legitimately fail on the destination chain (e.g. destination-side revert, insufficient liquidity, or any transient failure honestly observed by UVs), and this is a routine, expected outcome, not an attack precondition. [1](#0-0) 

On this path, `CallPRC20Deposit` re-mints the bridged amount back to `recipient` (attacker/user-controlled via `RevertInstructions.FundRecipient`, `x/uexecutor/keeper/outbound.go:107-112`). If that EVM call errors for any reason (e.g. token config edge cases, PRC20 contract-level restriction, gas exhaustion for `DerivedEVMCall`'s default gas limit since `CallPRC20Deposit` passes `nil` gasLimit, or any transient EVM failure), the outbound is marked `Status_ABORTED` via `AbortOutbound` and the function returns — no funds are recovered, and no further automatic remediation happens. [2](#0-1) 

The only path back to recovering stuck value is `AttachRescueOutboundFromReceipt`, but its eligibility check only accepts:
1. CEA inbounds whose *first* `PcTx` (the initial deposit) is `FAILED`, or
2. Non-CEA inbounds that have a `TxType_INBOUND_REVERT` outbound with `Status_REVERTED`.

An `ABORTED` outbound of type `FUNDS`/`GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD` produced by a re-mint failure satisfies neither condition — it is not the inbound's first `PcTx`, and its `TxType` is not `INBOUND_REVERT`. Consequently `AttachRescueOutboundFromReceipt` will unconditionally reject any rescue attempt for this UTX with `"rescue: UTX %s has no reverted inbound-revert outbound"` (or the CEA-deposit variant), even though the on-chain gateway-side `rescueFunds` EVM call ostensibly exists to recover exactly this kind of stuck balance. [3](#0-2) 

### Impact Explanation
This results in a permanent, protocol-level freeze of user funds: the original tokens were burned/withdrawn on Push Chain to initiate the outbound, the destination-chain transfer failed (so funds never arrived there), and the Push Chain-side compensating re-mint also failed — leaving the user with neither the destination funds nor the PC-side PRC20 balance, and no code path (automatic or admin-driven rescue) can recognize and restore it. This is a genuine "permanent loss / permanent freezing of user funds" impact in the Push Chain L1 scope, reachable purely from an honestly-observed failure of a normal user withdrawal — no malicious validator, admin, or privileged actor is required to trigger the precondition (a legitimately failing outbound plus a re-mint call that errors).

### Likelihood Explanation
The trigger requires two independent, unprivileged-observable conditions: (1) an outbound legitimately fails on the destination chain (which can happen for ordinary operational reasons, and can potentially be engineered by an attacker who controls the destination-chain interaction/payload to force a revert), and (2) the compensating `CallPRC20Deposit` re-mint also fails. Since `CallPRC20Deposit` is invoked with `gasLimit=nil` (default) and a recipient address fully controlled by the attacker via `RevertInstructions.FundRecipient`/inbound `Sender`, an attacker has some influence over conditions that could cause the re-mint call to fail deterministically. Given the append-only nature of `UniversalTx` and the strict rescue-eligibility gate, once this state is reached it is permanent with today's code.

### Recommendation
1. Extend `AttachRescueOutboundFromReceipt`'s eligibility check in `x/uexecutor/keeper/create_outbound.go` to also recognize `Status_ABORTED` outbounds of type `FUNDS`, `GAS_AND_PAYLOAD`, and `FUNDS_AND_PAYLOAD` (i.e., re-mint failures from `handleFailedOutbound`), not only the CEA-deposit-failed and non-CEA-INBOUND_REVERT-reverted cases.
2. Alternatively/additionally, add a retry mechanism inside `handleFailedOutbound` so a transient `CallPRC20Deposit` failure does not immediately and irrevocably abort the outbound.
3. Add an integration test analogous to `test/integration/uexecutor/rescue_funds_test.go` that forces `CallPRC20Deposit` to fail inside `handleFailedOutbound` and verifies a `RESCUE_FUNDS` outbound can subsequently be attached and successfully drains the stuck balance.

### Proof of Concept
1. Submit a normal `FUNDS_AND_PAYLOAD` inbound and drive it to a successful `TxType_FUNDS`/`FUNDS_AND_PAYLOAD` outbound (e.g., following `test/integration/uexecutor/inbound_initiated_outbound_test.go`'s setup).
2. Have UVs vote `MsgVoteOutbound` with `success=false` (an honest report that the destination-chain leg failed) — this enters `handleFailedOutbound` (`x/uexecutor/keeper/outbound.go:102`).
3. Mock/force `CallPRC20Deposit` to return an error (e.g., by pointing `outbound.Prc20AssetAddr` at an address that reverts on `depositPRC20Token`, similar to how `setupRescueFundsTest` in `test/integration/uexecutor/rescue_funds_test.go:94-95` uses an unregistered asset to force a deposit failure) — the outbound becomes `Status_ABORTED` with `AbortReason` containing `"failed to re-mint tokens for revert"`.
4. Attempt `AttachRescueOutboundFromReceipt` against this UTX (as in `rescue_funds_test.go`) — observe it returns an error (`"CEA deposit did not fail"` or `"has no reverted inbound-revert outbound"`) because neither eligibility branch matches an `ABORTED` `FUNDS_AND_PAYLOAD` outbound, proving no rescue path exists and the funds are permanently stuck.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L119-137)
```go
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L239-262)
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
		} else {
			hasRevertedAutoRevert := false
			for _, ob := range originalUtx.OutboundTx {
				if ob != nil && ob.TxType == types.TxType_INBOUND_REVERT && ob.OutboundStatus == types.Status_REVERTED {
					hasRevertedAutoRevert = true
					break
				}
			}
			if !hasRevertedAutoRevert {
				return fmt.Errorf("rescue: UTX %s has no reverted inbound-revert outbound", originalUtxId)
			}
		}
```

**File:** proto/uexecutor/v1/types.proto (L76-93)
```text
enum Status {
  UNSPECIFIED = 0;
  PENDING = 1;
  OBSERVED = 2;
  REVERTED = 3;
  ABORTED = 4;    // finalization or revert attachment failed — requires manual intervention
}

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
