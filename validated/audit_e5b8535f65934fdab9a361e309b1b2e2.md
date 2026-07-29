## Finding Confirmed

The exploit claim is technically accurate at the code level, but it doesn't require an "attacker" in the sense the scope demands — it's a deterministic accounting bug that fires on every EIP-1559 (type-2) outbound transaction, not something an unprivileged party needs to specially trigger. Still, since it corrupts on-chain refund accounting reachable through the ordinary outbound/refund flow, it meets the impact gate.

### Title
Incorrect use of `tx.GasPrice()` instead of `receipt.EffectiveGasPrice` corrupts `GasFeeUsed`, causing systematic under-refund of gas to depositors - (`universalClient/chains/evm/event_confirmer.go`)

### Summary
In `processPendingEvents`, when an outbound event is confirmed, the gas fee actually consumed is computed as `tx.GasPrice() * receipt.GasUsed` [1](#0-0)  instead of `receipt.EffectiveGasPrice * receipt.GasUsed`. For EIP-1559 (dynamic-fee) transactions, go-ethereum's `Transaction.GasPrice()` returns the transaction's `GasFeeCap` (the max fee the sender is willing to pay), not the effective price actually charged (`baseFee + priorityFee`, capped at `GasFeeCap`). Since `GasFeeCap >= EffectiveGasPrice` by construction, this overstates `gasFeeUsed`.

### Finding Description
The computed `gasFeeUsed` is stored into `OutboundEvent.GasFeeUsed` and persisted [2](#0-1) . This value later flows into the outbound observation consumed on-chain by `applyGasRefund` in `x/uexecutor/keeper/outbound.go`, which computes `refundAmount := gasFee - gasFeeUsed` and only refunds if `gasFee > gasFeeUsed` [3](#0-2) . Because `gasFeeUsed` is inflated whenever the destination chain's base fee is below the fee cap the tx builder chose (i.e., in the normal/common case), the refunded excess-gas amount is understated or zeroed out entirely.

### Impact Explanation
This corrupts gas-fee/refund accounting reachable through the standard deposit → outbound → refund flow (no malicious peer/validator/relayer needed), matching "corruption of gas fee accounting, refund accounting" in the allowed impact list. The practical effect is depositors receiving less refunded gas than they are entitled to (or none at all when the overstatement exceeds the true excess), i.e., partial loss of user funds accumulating in the module rather than being returned.

### Likelihood Explanation
This triggers deterministically for any EIP-1559 outbound transaction where the base fee paid is below the configured fee cap — which is the common case for dynamic-fee transactions on EVM chains. It does not require any malicious actor; it is a systemic correctness bug in the fee-accounting logic that fires whenever ordinary outbound refunds are processed.

### Recommendation
Use `receipt.EffectiveGasPrice` (available on the transaction receipt post EIP-1559) instead of `tx.GasPrice()` when computing `gasFeeUsed`:
```go
gasUsed := new(big.Int).SetUint64(receipt.GasUsed)
gasPrice := receipt.EffectiveGasPrice
gasFeeUsed := new(big.Int).Mul(gasUsed, gasPrice).String()
```

### Proof of Concept
Construct a dynamic-fee (type-2) transaction with `GasFeeCap = 100 gwei` but network `BaseFee = 20 gwei` and `GasTipCap = 2 gwei`, so `EffectiveGasPrice = 22 gwei`. Call `processPendingEvents` against a receipt with `Status = 1`, `GasUsed = 21000`, `EffectiveGasPrice = 22 gwei`. The stored `outboundEvent.GasFeeUsed` will equal `21000 * 100 gwei` (from `tx.GasPrice()`) rather than `21000 * 22 gwei` (from `receipt.EffectiveGasPrice`), demonstrating the ~4.5x overstatement that feeds into `applyGasRefund`'s refund computation. [4](#0-3) [5](#0-4)

### Citations

**File:** universalClient/chains/evm/event_confirmer.go (L168-203)
```go
			// For outbound events, enrich with gas fee before confirming
			if event.Type == store.EventTypeOutbound {
				tx, _, txErr := ec.rpcClient.GetTransactionByHash(ctx, hash)
				if txErr != nil {
					ec.logger.Warn().
						Err(txErr).
						Str("event_id", event.EventID).
						Str("tx_hash", txHash).
						Msg("failed to fetch transaction for gas fee, skipping confirmation")
					continue
				}
				gasUsed := new(big.Int).SetUint64(receipt.GasUsed)
				gasPrice := tx.GasPrice()
				gasFeeUsed := new(big.Int).Mul(gasUsed, gasPrice).String()

				// Unmarshal, set GasFeeUsed, re-marshal
				var outboundEvent chaincommon.OutboundEvent
				if unmarshalErr := json.Unmarshal(event.EventData, &outboundEvent); unmarshalErr != nil {
					ec.logger.Error().
						Err(unmarshalErr).
						Str("event_id", event.EventID).
						Msg("failed to unmarshal outbound event data")
					continue
				}
				outboundEvent.GasFeeUsed = gasFeeUsed

				updatedData, marshalErr := json.Marshal(outboundEvent)
				if marshalErr != nil {
					ec.logger.Error().
						Err(marshalErr).
						Str("event_id", event.EventID).
						Msg("failed to marshal enriched outbound event data")
					continue
				}

				rowsAffected, err = ec.chainStore.UpdateStatusAndEventData(event.EventID, store.StatusPending, store.StatusConfirmed, updatedData)
```

**File:** x/uexecutor/keeper/outbound.go (L178-198)
```go
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
```
