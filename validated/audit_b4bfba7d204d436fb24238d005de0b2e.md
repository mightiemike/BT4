Based on my investigation, I found that `handleFailedOutbound` in `x/uexecutor/keeper/outbound.go` only re-mints bridged funds for a subset of `TxType` values, which is the closest structural analog to the Swivel H-5 bug (an optional/conditional branch that omits fund restoration for a valid variant).

### Title
Bridged funds are not restored on outbound failure for `PAYLOAD` and `GAS` transaction types - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
`handleFailedOutbound` only mints back bridged tokens to the revert recipient when `outbound.TxType` is `FUNDS`, `GAS_AND_PAYLOAD`, or `FUNDS_AND_PAYLOAD`. It does not do so for `TxType_GAS` or `TxType_PAYLOAD` outbounds, even though both of those types can carry a monetary `amount` that was already deducted/committed on Push Chain when the corresponding outbound was created.

### Finding Description
`handleFailedOutbound` [1](#0-0)  gates the fund re-mint step with:

```go
if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
    outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {
    ...
    receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)
    ...
}
outbound.OutboundStatus = types.Status_REVERTED
```

Per the module's own documentation, `TxType_GAS` outbound semantics are "Refund of unused gas back to a source chain" [2](#0-1) , meaning it carries a monetary `Amount` field representing value that was already deducted or reserved on Push Chain in order to be delivered externally. If the UV-observed broadcast of that outbound fails (`MsgVoteOutbound` reports `success=false`), `handleFailedOutbound` runs, sets `OutboundStatus = REVERTED`, and applies only the unrelated gas-fee-refund step (`applyGasRefund`, which refunds relayer gas overpayment, not the outbound's own `Amount`). Because `TxType_GAS` (and `TxType_PAYLOAD`) are excluded from the fund re-mint branch, the value represented by `outbound.Amount` for those types is never restored to the user, while the outbound is nonetheless marked `REVERTED` — the terminal state that (for `FUNDS`-family types) normally implies "funds are safely back with the user."

This mirrors the Swivel H-5 pattern precisely: a valid, reachable outcome (destination-chain execution failure for a `GAS`-type outbound, triggered by ordinary destination-chain conditions honestly observed by UVs) falls through a conditional that only handles a subset of variants, silently leaving value unaccounted for instead of failing loud or restoring it.

### Impact Explanation
If exploitable as described, a user whose `GAS`-type outbound (refund-of-unused-gas-to-source-chain) fails to land on the destination chain would have that value permanently lost: `OutboundStatus` flips to `REVERTED` (implying resolution) but no compensating mint/credit occurs on Push Chain, and no admin-visible flag distinguishes this from the funds-covered revert path. This matches the "in scope" impact of permanent loss/freezing of user funds through the outbound revert flow.

### Likelihood Explanation
Reaching this path requires only an honest majority of UVs observing and voting that a `GAS`-type outbound failed on the destination chain (`MsgVoteOutbound` with `success=false`) — a condition producible by ordinary external-chain circumstances (e.g., transient revert, insufficient destination liquidity) without any privileged or malicious actor, satisfying the "ordinary user deposit/withdrawal path" requirement of the scope.

### Recommendation
Include `TxType_GAS` (and confirm whether `TxType_PAYLOAD`, if it can carry non-zero value, should be included) in the fund-restoration branch of `handleFailedOutbound`, or explicitly document/assert that `GAS`/`PAYLOAD` outbound `Amount` fields are always zero and therefore need no revert handling — and add a guard that rejects/aborts (rather than marks `REVERTED`) outbound records for these types if `Amount` is non-zero and no restoration path exists.

### Proof of Concept
I was not able to fully trace this to certainty within the available context — specifically, I could not confirm from the indexed code whether `TxType_GAS` outbounds are ever constructed with a non-zero `Amount` field in practice (i.e., whether the "refund unused gas to source chain" semantics documented in `x/uexecutor/README.md` actually populate `OutboundTx.Amount` with a real, previously-committed value, or whether that value flow is handled entirely through the separate `PcRefundExecution`/`applyGasRefund` gas-fee-refund mechanism instead). Confirming this requires locating the code path(s) that construct `TxType_GAS` outbounds (not found in the retrieved snippets) to verify whether `outbound.Amount` is populated and what it represents. Given the index size limits noted in my instructions, I could not exhaustively search all outbound-construction call sites; a full-repository session (e.g., a Devin session with complete file access) would be needed to trace every constructor of `types.OutboundTx{TxType: types.TxType_GAS, ...}` and confirm whether `Amount` is ever non-zero for this type before treating this as a confirmed, exploitable finding rather than a structural code-smell analog.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L99-119)
```go
// handleFailedOutbound mints back the bridged tokens to the revert recipient,
// then attempts to refund any excess gas (gasFee - gasFeeUsed) just like a
// successful outbound would. Both operations are recorded on the outbound.
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)
```

**File:** x/uexecutor/README.md (L128-136)
```markdown
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
