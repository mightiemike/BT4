### Title
Unprivileged attacker can permanently freeze bridged funds by setting `RevertInstructions.FundRecipient` to an address that causes PRC20 deposit to revert, aborting the outbound with no on-chain recovery path - ([File: x/uexecutor/keeper/outbound.go])

### Summary
`handleFailedOutbound` in `x/uexecutor/keeper/outbound.go` re-mints bridged tokens to a `recipient` derived from `outbound.RevertInstructions.FundRecipient` (an attacker-controlled field originating from the user's own inbound `RevertInstructions`). If this single `CallPRC20Deposit` transfer reverts, the entire failed-outbound handling path aborts and the funds become permanently stuck in `ABORTED` status, requiring privileged/manual intervention — the exact "one-asset-transfer-failure blocks the whole flow" pattern from the M-3 report, mapped onto Push Chain's outbound-revert path instead of Sentiment's `sweepTo` liquidation loop.

### Finding Description
`RevertInstructions.FundRecipient` is populated straight from the source-chain gateway event data (`event.RevertRecipient` in `x/uexecutor/keeper/create_outbound.go` line ~87, and similarly for inbound reverts in `x/uexecutor/keeper/build_revert_outbound.go`), i.e. it is fully attacker-controlled — the user who initiates the crosschain call chooses this address. [1](#0-0) 

When an outbound is voted as failed by UVs, `FinalizeOutbound` → `handleFailedOutbound` re-mints the PRC20 to that same attacker-chosen recipient: [2](#0-1) 

If `CallPRC20Deposit` reverts — which a PRC20/handler-contract call can do for many benign or attacker-inducible reasons (e.g., the "recipient" is a contract without a `receive`/fallback compatible with the deposit call flow, a blacklisted/paused underlying representation, or any other transfer-time revert condition analogous to the weird-ERC20 cases cited in M-3) — the code takes the single-failure branch and immediately calls `AbortOutbound`, which sets `OutboundStatus = ABORTED` and returns, with **no further attempt** to try an alternate recipient (e.g., the original sender) or to retry automatically: [3](#0-2) 

The module's own documentation confirms `ABORTED` has no automatic recovery: it is explicitly a dead end reachable only through operator/governance-driven manual resolution, not chain-driven: [4](#0-3) [5](#0-4) 

This mirrors M-3 exactly: a single-asset transfer failure (one `CallPRC20Deposit`/mint call, analogous to Sentiment's `sweepTo` per-asset transfer) blocks the entire remediation flow (the revert-mint that is supposed to make the user whole after a failed outbound), and there is no fallback such as skipping/retrying with a safe default recipient (e.g., falling back to `outbound.Sender`, the derived UEA address, which is guaranteed to accept PRC20 mints).

### Impact Explanation
An unprivileged user who submits a crosschain transaction with an outbound (`FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS_AND_PAYLOAD`) can choose `RevertInstructions.FundRecipient` (or, in the `INBOUND_REVERT` case, the same field on the inbound) to be an address engineered to make `depositPRC20Token`/`CallPRC20Deposit` revert. If the corresponding outbound subsequently fails on the destination chain (attacker can also control this, e.g. by targeting a destination chain/asset pair where execution predictably reverts, or simply waiting for organic failure), the revert-mint step reverts, and `AbortOutbound` permanently marks the outbound `ABORTED` with no automatic re-mint, refund, or retry path. The bridged funds are effectively frozen — they are neither delivered to the destination chain nor returned to the user — until a privileged operator intervenes. This satisfies the in-scope impact "permanent freezing... of user or protocol-controlled funds" reachable via an unprivileged user's own transaction and honest validator voting.

### Likelihood Explanation
Medium-High. No privileged party or malicious validator/relayer is needed — an ordinary user constructs the crosschain payload with an unfavorable `FundRecipient`, and the failure is triggered by honest UVs voting the (possibly organically) failed outbound observation via the standard `MsgVoteOutbound` path. The main variable is whether a destination-chain outbound naturally or attacker-influenceably fails at the same time the corresponding PC-side re-mint recipient reverts; this is plausible for CEA/contract-based `FundRecipient`s that intentionally reject unexpected token deposits, similar to the "blocked address / paused token / reverting recipient" scenarios enumerated in the M-3 report.

### Recommendation
Do not let a single re-mint call block the whole finalize/abort flow irreversibly. Options, consistent with the M-3 remediation ("catch reversions and skip/retry"):
- Wrap the `CallPRC20Deposit` re-mint in a fallback: if the deposit to `RevertInstructions.FundRecipient` fails, retry once against the guaranteed-valid fallback address (e.g., `outbound.Sender`'s derived UEA) before aborting.
- Alternatively, keep the outbound `ABORTED` for audit purposes but automatically queue a retryable "rescue" style re-mint attempt (similar to the existing `RESCUE_FUNDS` outbound machinery) rather than requiring purely manual/governance intervention, so the funds are not indefinitely frozen behind an off-chain-only recovery process.
- Validate `RevertInstructions.FundRecipient` at inbound/outbound creation time to ensure it is capable of receiving PRC20 deposits (e.g., reject contract addresses without a known-safe receive path), reducing the attacker's ability to weaponize this field against themselves or others.

### Proof of Concept
1. Attacker submits a source-chain gateway transaction producing a `FUNDS_AND_PAYLOAD` inbound whose payload, when executed, emits a `UniversalTx` outbound event with `revertRecipient` set to a contract address `C` that the attacker controls and that reverts on `depositPRC20Token`'s internal transfer/mint call (e.g., a contract with no PRC20-compatible receive hook, or one that reverts unconditionally).
2. `BuildOutboundsFromReceipt` builds the `OutboundTx` with `RevertInstructions.FundRecipient = C` [6](#0-5) , entered as `PENDING` and added to `PendingOutbounds`.
3. UVs sign and broadcast the outbound to the destination chain; the destination-chain call fails for any reason (attacker can pick a destination chain/contract call that reliably reverts) and honest UVs vote `MsgVoteOutbound` with `success=false`.
4. `VoteOutbound` → `FinalizeOutbound` → `handleFailedOutbound` attempts `CallPRC20Deposit(prc20, C, amount)` to return the bridged funds; this reverts because `C` is designed to reject the deposit.
5. `AbortOutbound` is called, setting `OutboundStatus = ABORTED` [3](#0-2) . The bridged tokens are now neither on the destination chain nor returned to any address on Push Chain, and per the module's documented design, no automatic chain-driven resolution exists [7](#0-6) .

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}
```

**File:** x/uexecutor/keeper/outbound.go (L102-119)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L130-137)
```go
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/README.md (L271-282)
```markdown
  per outbound indicate validator divergence on the destination-chain
  observation (different `success`/`tx_hash`/`error_msg`/`gas_fee_used`).
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```

**File:** proto/uexecutor/v1/types.proto (L76-82)
```text
enum Status {
  UNSPECIFIED = 0;
  PENDING = 1;
  OBSERVED = 2;
  REVERTED = 3;
  ABORTED = 4;    // finalization or revert attachment failed — requires manual intervention
}
```
