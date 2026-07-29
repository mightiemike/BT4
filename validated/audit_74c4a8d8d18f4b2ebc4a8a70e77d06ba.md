### Title
Solana-bound outbound funds can be permanently stranded when accumulated PRC20 balance/withdraw amount exceeds `uint64` max — ([File: universalClient/chains/svm/tx_builder.go])

### Summary
Push Chain accepts an unbounded number of independent inbound deposits into the same recipient's PRC20 balance with no cross-deposit ceiling, and `OutboundTx.ValidateBasic` only requires the amount to be a "valid positive uint256" — no upper bound tied to the destination chain's native numeric type. When that PRC20 balance is later withdrawn to a Solana-family destination chain, the relayer-side (`universalClient`) transaction builder rejects any amount that doesn't fit in a `uint64`. Because the burn/withdraw already happened atomically on the Push Chain EVM side before the outbound reaches the relayer, an amount that exceeds `u64` max can never be broadcast to Solana, and there is no split/retry mechanism — the bridged value is permanently unrecoverable from the user's perspective, mirroring the code4rena Lido finding where a per-call cap check is bypassed by cumulative multi-call state and the resulting position becomes permanently stuck.

### Finding Description
1. `x/uexecutor` allows the same recipient to receive many independent inbound `FUNDS`/`FUNDS_AND_PAYLOAD` deposits, each individually validated but with **no aggregate cap** on the resulting PRC20 balance. This is explicitly exercised by the "multiple solana FUNDS inbounds accumulate balance" and "multiple inbounds accumulate balances" tests, which show balance strictly accumulates across separate `TxHash` inbound events with no ceiling check: [1](#0-0) 

2. `OutboundTx.ValidateBasic` only enforces that `Amount` is a positive `uint256`-parseable string — there is no check against the numeric range of the destination VM (e.g. Solana's native `u64` amount field): [2](#0-1) 

3. Outbounds toward Solana carry the amount verbatim from the `UniversalTxOutboundEvent` emitted by the Push Chain gateway contract when tokens are burned/withdrawn on the EVM side, i.e., the burn already occurred by the time the `OutboundTx` exists on-chain: [3](#0-2) 

4. Only at broadcast time — deep in the relayer's Solana transaction builder — is the amount finally checked against `uint64`, and if it doesn't fit, the transaction build fails outright with no fallback: [4](#0-3) [5](#0-4) 

5. Unlike the fund-migration path (`BroadcastFundMigrationTx`) which sweeps a single computed maximum with no accumulation concern, or the Solana ref-route mechanism which splits *oversized transaction bytes* (not oversized numeric amounts) into a two-transaction flow, there is **no equivalent splitting mechanism for an outbound amount that exceeds `u64`**. The ref-route logic only addresses Solana's 1232-byte transaction size limit, not the numeric range of the `amount` field: [6](#0-5) 

This is the same class of bug as the Lido `MAX_STETH_WITHDRAWAL_AMOUNT` finding: a single-call bound (`protocolDeposit`'s per-call cap / here, `IsUint64()`'s per-broadcast cap) is technically sound in isolation, but the system permits unbounded accumulation across multiple valid calls (multiple stakes / multiple inbound deposits) into one position that must later be unwound atomically. Once the accumulated total crosses the single-call bound, the withdrawal can never complete, and (in the Lido case) the position is unstakeable; here, the withdraw already burned the PRC20 balance on Push Chain, so the outbound sits `PENDING` forever and the funds are unrecoverable via normal automated flows.

### Impact Explanation
Once tokens are burned via the Push Chain gateway (creating the `OutboundTx` with `Amount > 2^64-1`), no honest validator, relayer, or automated retry can ever successfully build/broadcast the corresponding Solana transaction — `BuildOutboundTransaction` and `BuildRefRouteTransactions` both hard-fail on `!amount.IsUint64()`. The outbound remains `PENDING` indefinitely; it is never `OBSERVED`, so `FinalizeOutbound`'s re-mint/refund path (which only triggers on `OBSERVED` outbounds with failed observation) is never reached either — there is no automatic revert-and-remint for a withdrawal that can't even be constructed. This constitutes a permanent, unprivileged loss/freezing of user-controlled funds, matching the in-scope "permanent freezing... of user or protocol-controlled funds" and "corruption of ... revert destination ... or canonical UniversalTx state" impact categories.

### Likelihood Explanation
Reaching `u64` max (`~1.8×10^19` raw units) requires either (a) very large legitimate deposit volume accumulated over many genuine, validator-observed inbound events to the same recipient over time, or (b) a PRC20 token whose native representation on Push Chain uses more decimals than the source asset (e.g., an 8-decimal source token normalized to an 18-decimal PRC20), which would substantially lower the number of real-world source-chain units needed to cross the threshold. The trigger is fully reachable by an ordinary unprivileged user (recipient) simply depositing repeatedly and later withdrawing to a Solana destination chain — no privileged or malicious-validator action is required, satisfying the "reachable without privileged control" scope requirement. I could not fully confirm within available context whether token decimal normalization amplifies raw on-chain values before reaching the PRC20 balance (the `x/uregistry` token-config decimal-scaling logic was not fully inspected), which affects how large a real-world deposit total is needed to trigger this; this should be verified directly against `x/uregistry/types/token_config.go` and the deposit/scaling code path.

### Recommendation
- Enforce a destination-chain-aware maximum on `OutboundTx.Amount` (and/or on cumulative PRC20 balance destined for `u64`-limited chains) in `x/uexecutor/types/outbound_tx.go::ValidateBasic` or at outbound-creation time in `create_outbound.go`, mirroring the recommendation from the Lido report to move the bound check to the point where the cumulative/aggregate state is known, not just at final broadcast.
- If an amount does exceed the destination chain's native numeric range, split the withdrawal into multiple `OutboundTx` entries (analogous to the existing Solana ref-route mechanism for oversized transaction *bytes*) rather than allowing the single burn+outbound to be created atomically and unrecoverably.
- Add an explicit `FAILED`/`ABORTED` state transition (with automatic re-mint) triggered when `BuildOutboundTransaction`/`BuildRefRouteTransactions` return the "amount exceeds u64 max" error, so the relayer can surface this as a recoverable condition (re-minting PRC20 back to the sender) instead of leaving the outbound stuck `PENDING` forever.

### Proof of Concept
1. Attacker (or ordinary user) deposits into the Push Chain gateway from an external chain multiple times to the same UEA/EOA recipient, each deposit individually valid and within any per-inbound checks; validators honestly observe and vote each inbound to quorum, as shown by the accumulation test pattern: [1](#0-0) 
2. Recipient's PRC20 balance accumulates past `2^64-1` raw units (facilitated further if the PRC20's native-representation decimals exceed the source token's decimals).
3. Recipient calls the Push Chain gateway's withdraw path targeting a Solana destination; the gateway burns the PRC20 balance and emits `UniversalTxOutboundEvent` with `Amount > uint64` max, and `BuildOutboundsFromReceipt` creates a `PENDING` `OutboundTx` with that amount, passing `ValidateBasic` because it's still a valid positive `uint256`: [3](#0-2) 
4. TSS signing proceeds and the relayer's Solana builder attempts `BuildOutboundTransaction`, which fails permanently: [7](#0-6) 
5. The outbound never reaches `OBSERVED`, so `FinalizeOutbound`'s re-mint-on-failure logic is never invoked, and the burned PRC20 value is permanently unrecoverable through any automated on-chain path.

### Citations

**File:** test/integration/uexecutor/inbound_solana_test.go (L167-189)
```go
	t.Run("multiple solana FUNDS inbounds accumulate balance", func(t *testing.T) {
		app, ctx, vals, inbound, coreVals := setupSolanaInboundTest(t, 4, uexecutortypes.TxType_FUNDS)

		ueModuleAccAddress, _ := app.UexecutorKeeper.GetUeModuleAddress(ctx)
		recipient := common.HexToAddress(inbound.Recipient)

		// First inbound
		voteToQuorum(t, ctx, app, vals, coreVals, inbound)

		// Second inbound with different tx hash
		inbound2 := *inbound
		inbound2.TxHash = "3kHu2qwD7q5xMkZxq6z2S3r4y5N7m8P9kL0jH1gF2dE"
		voteToQuorum(t, ctx, app, vals, coreVals, &inbound2)

		// Balance should be 2x
		res, err := app.EVMKeeper.CallEVM(ctx, prc20ABI, ueModuleAccAddress, prc20Address, false, nil, "balanceOf", recipient)
		require.NoError(t, err)
		balances, _ := prc20ABI.Unpack("balanceOf", res.Ret)
		expected := new(big.Int)
		expected.SetString(inbound.Amount, 10)
		expected.Mul(expected, big.NewInt(2))
		require.Equal(t, 0, balances[0].(*big.Int).Cmp(expected))
	})
```

**File:** x/uexecutor/types/outbound_tx.go (L55-63)
```go
	// amount validation (only for funds-related txs)
	if p.TxType == TxType_FUNDS || p.TxType == TxType_FUNDS_AND_PAYLOAD {
		if strings.TrimSpace(p.Amount) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty for funds tx")
		}
		if bi, ok := new(big.Int).SetString(p.Amount, 10); !ok || bi.Sign() <= 0 {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid positive uint256")
		}
	}
```

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

**File:** universalClient/chains/svm/tx_builder.go (L698-707)
```go
	// --- Re-parse event data (same parsing as GetOutboundSigningRequest) ---

	amount := new(big.Int)
	amount, ok := amount.SetString(data.Amount, 10)
	if !ok {
		return nil, 0, fmt.Errorf("invalid amount: %s", data.Amount)
	}
	if !amount.IsUint64() {
		return nil, 0, fmt.Errorf("amount exceeds u64 max: %s", data.Amount)
	}
```

**File:** universalClient/chains/svm/tx_builder.go (L995-1019)
```go
// =============================================================================
//  STEP 2b: BuildRefRouteTransactions
//
//  For execute-mode outbounds whose direct finalize_universal_tx exceeds
//  Solana's 1232-byte limit, the universal validator splits the work into
//  two transactions:
//
//    1. store_execute_ix_data — relayer-signed only (no TSS involvement);
//       uploads raw ix_data into a content-addressed PDA.
//    2. finalize_universal_tx_with_ix_data_ref — uses the SAME TSS signature
//       as the direct route; gateway reconstructs the message from stored bytes.
//
//  NOTE: parsing duplicates BuildOutboundTransaction. Future refactor should
//  hoist the parse into a shared helper. For now the duplication is bounded
//  to execute mode (id=2); revert/rescue (3/4) never use this path.
// =============================================================================

// BuildRefRouteTransactions builds the (storeTx, refFinalizeTx) pair for a
// large-payload execute outbound. Only valid for instructionID=2 with non-empty
// ix_data; callers should size-check the direct tx first and only invoke this
// when the direct route doesn't fit.
//
// Returns the storedIxData PDA alongside the txs so the broadcaster can probe
// for pre-existing PDAs (retry idempotency) before re-broadcasting the store tx.
func (tb *TxBuilder) BuildRefRouteTransactions(
```

**File:** universalClient/chains/svm/tx_builder.go (L1043-1052)
```go
	// --- Parse event (mirrors BuildOutboundTransaction; execute path only) ---

	amount := new(big.Int)
	amount, ok := amount.SetString(data.Amount, 10)
	if !ok {
		return nil, nil, solana.PublicKey{}, fmt.Errorf("invalid amount: %s", data.Amount)
	}
	if !amount.IsUint64() {
		return nil, nil, solana.PublicKey{}, fmt.Errorf("amount exceeds u64 max: %s", data.Amount)
	}
```
