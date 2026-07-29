Based on my research, I did not find a `Pausable`/`whenNotPaused`-style contract in this repo (no `LpToken.sol` analog exists here), but I found a close structural analog to the reported bug class: **a single "enabled" gate used to block both new outbound creation and legitimate reserved fund flows**, in `x/uexecutor/keeper/create_outbound.go`.

### Title
Outbound-disabled gate in `BuildOutboundsFromReceipt` can permanently strand already-burned PRC20 funds instead of only blocking new outbound creation - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
The reported bug pattern is: a pause switch intended only for new deposits also blocks withdrawals, causing user funds to be inaccessible. The closest analog in this repository is the single `IsChainOutboundEnabled` check inside `BuildOutboundsFromReceipt`, which gates *all* outbound creation uniformly — it does not distinguish between a brand-new outbound requested by a user payload (e.g. calling the gateway's withdraw path) and the accounting record that must exist for tokens already burned/consumed on the EVM side in the same transaction.

### Finding Description
`BuildOutboundsFromReceipt` scans the EVM receipt logs for `UniversalTxOutboundEventSig` events emitted by the gateway contract, and for each one checks: [1](#0-0) 
If `IsChainOutboundEnabled` returns false for the destination chain, the entire function returns an error rather than skipping just that log. This function is invoked from `AttachOutboundsToExistingUniversalTx` and `CreateUniversalTxFromReceiptIfOutbound`, which are called after an EVM receipt has already been produced by `CallExecuteUniversalTx` (e.g. in `execute_inbound_funds_and_payload.go`) — i.e., after the user's payload has already executed on the EVM side (potentially burning/locking the PRC20 balance and emitting the outbound event as proof of that action). [2](#0-1) 

If the outbound-record creation fails at this later Cosmos-keeper stage because the destination chain has since been marked outbound-disabled, the caller returns an error up the stack. Whether the already-executed EVM state (the token burn implied by the gateway's outbound event) is rolled back together with this failure, or whether it was already committed via `writeCache()` prior to this check, determines whether this is "merely" a denial of service (transaction fails, retried later) or a genuine fund-loss bug (tokens burned on EVM side, but no `OutboundTx`/`PendingOutbounds` entry ever created to track or refund them). I was not able to fully trace every call site to confirm which branch definitively applies in all TxType paths (`FUNDS_AND_PAYLOAD` smart-contract path vs `GAS_AND_PAYLOAD` vs UEA payload path), so this remains a partially-verified concern rather than a confirmed exploit.

Separate from the atomicity question, the *design* mirrors the reported bug directly: a single boolean flag (`ChainConfig.Enabled.IsOutboundEnabled`) is used indiscriminately to gate both (a) brand-new outbound creation that should legitimately be paused, and (b) the bookkeeping needed to preserve already-locked/burned user funds in-flight. The report's recommendation — "only use pausable mechanisms for deposit functions, avoid using them for withdrawals" — applies equally here: outbound creation for already-executed EVM actions (funds already moved/burned on the Push Chain side) should not be subject to the same disable switch as freshly-initiated outbound requests.

### Impact Explanation
If the EVM-side burn is not rolled back atomically with the Cosmos-side outbound-record failure, this would constitute unauthorized permanent loss of user-controlled funds with no compensating `OutboundTx` or refund path recorded — the funds simply vanish from the UTX bookkeeping. Even in the more benign case (full atomic rollback), it still constitutes a denial-of-service on withdrawal-type flows using the same switch meant for new-deposit pausing, consistent with the reported bug class.

### Likelihood Explanation
Note that `IsChainOutboundEnabled` is controlled by `x/uregistry` admin-only messages (`MsgUpdateChainConfig`), not by an unprivileged attacker directly. Per the scoping rules, "admin or governance abuse" is explicitly out of scope, and disabling this flag is an admin action, not something an attacker can trigger. This weakens the applicability of the finding under the strict "unprivileged external attacker" gate — the precondition (an admin/ops team pausing outbound for a chain, e.g. as a legitimate security response) is not attacker-controlled, only the resulting fund-freeze/loss the ordinary user experiences is. I am flagging this as the most direct structural analog found in the scoped code, but I could not confirm a fully attacker-triggerable (no privileged precondition) path matching the report's bug class within the time available.

### Recommendation
- Separate the "block new outbound requests" check from "record an outbound for EVM state that already executed/burned funds." The latter should never be blocked by `IsChainOutboundEnabled`; it should always succeed in creating the bookkeeping record (or fail safe by reverting the entire EVM call atomically before any burn is committed).
- Audit all call sites of `BuildOutboundsFromReceipt` to confirm whether the EVM state mutation (`CallExecuteUniversalTx`) and the outbound-attachment failure are wrapped in the same `CacheContext`/`writeCache()` boundary, ensuring no burn can be committed without a corresponding `OutboundTx` record.

### Proof of Concept
Not independently reproduced — this requires confirming, via a test harness, whether disabling `IsOutboundEnabled` for a chain after a user's `MsgExecutePayload` has already caused an EVM-side token burn (gateway outbound event emitted) results in committed EVM state with no corresponding `OutboundTx`/`PendingOutbounds` entry. I was unable to fully trace this atomicity boundary within the available search iterations. [3](#0-2) [4](#0-3)

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L16-57)
```go
func (k Keeper) BuildOutboundsFromReceipt(
	ctx context.Context,
	utxId string,
	receipt *evmtypes.MsgEthereumTxResponse,
) ([]*types.OutboundTx, error) {

	outbounds := []*types.OutboundTx{}
	universalGatewayPC := strings.ToLower(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address)

	k.Logger().Debug("building outbounds from receipt", "utx_id", utxId, "tx_hash", receipt.Hash, "log_count", len(receipt.Logs))

	for _, lg := range receipt.Logs {
		if lg.Removed {
			continue
		}

		if strings.ToLower(lg.Address) != universalGatewayPC {
			continue
		}

		if len(lg.Topics) == 0 {
			continue
		}

		if strings.ToLower(lg.Topics[0]) != strings.ToLower(types.UniversalTxOutboundEventSig) {
			continue
		}

		event, err := types.DecodeUniversalTxOutboundFromLog(lg)
		if err != nil {
			return nil, fmt.Errorf("failed to decode UniversalTxWithdraw: %w", err)
		}

		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L144-155)
```go
func (k Keeper) AttachOutboundsToExistingUniversalTx(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	utx types.UniversalTx,
) error {
	outbounds, err := k.BuildOutboundsFromReceipt(ctx, utx.Id, receipt)
	if err != nil {
		return err
	}

	return k.attachOutboundsToUtx(ctx, utx.Id, outbounds, "")
}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L239-255)
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
```
