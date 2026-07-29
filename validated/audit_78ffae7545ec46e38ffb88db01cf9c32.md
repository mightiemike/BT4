### Title
Unguarded `big.Int.Uint64()` truncation of outbound amount in SVM TSS-signing path can permanently freeze bridged funds - (File: universalClient/chains/svm/tx_builder.go)

### Summary
The SVM outbound signing-request builder in `universalClient/chains/svm/tx_builder.go` calls `amount.Uint64()` directly on an attacker/user-influenced `*big.Int` amount at line 381 (`if amount.Uint64() == 0 { ... }`) and again at line 399 (`constructTSSMessage(..., amount.Uint64(), ...)`) without first checking `amount.IsUint64()`. This mirrors the FPAM.sol root cause: a value that can legitimately exceed the target integer width (`uint96` there, `uint64` here) is narrowed via unchecked cast, silently wrapping instead of erroring. Go's `big.Int.Uint64()` returns the value mod 2^64 when the receiver doesn't fit, exactly analogous to Solidity's silent `uint96` truncation before compiler-enforced overflow checks were added.

### Finding Description
`OutboundTx.Amount` (and the underlying `event.Amount` from `BuildOutboundsFromReceipt`, [1](#0-0) ) is stored as a decimal string with no upper bound relative to `uint64.MaxUint64` (~1.8446e19). PRC20 tokens can be represented with 18 decimals, so any bridged amount above ~18.44 whole tokens exceeds `uint64` range.

In the SVM `TxBuilder`'s outbound signing-request path, the amount is parsed into a `*big.Int` and then narrowed unsafely: [2](#0-1) [3](#0-2) 

This truncated `uint64` value is baked directly into the keccak256 message that TSS validators sign (`constructTSSMessage`), and into the Borsh instruction data at broadcast time (`buildWithdrawAndExecuteData`, `buildRevertData`, `buildRescueData`), i.e. the wire amount embedded in signatures/instructions is `amount mod 2^64`.

Notably, a *different* function — `BuildOutboundTransaction`, which re-parses the same `data.Amount` at broadcast time — *does* guard this: [4](#0-3) 

Because the signing-request path (used to produce the value TSS actually signs) lacks this guard while the broadcast path enforces it, any outbound with `amount >= 2^64` will:
1. Have TSS validators produce a valid, honestly-signed hash over an amount that is silently wrapped (not the real amount), and
2. Then permanently fail at `BuildOutboundTransaction`/`BroadcastOutboundSigningRequest` because `IsUint64()` now (correctly) rejects the same amount.

The failure is deterministic and will recur on every retry (nonce reassignment does not change the amount), so the outbound can never be completed or reverted through this code path.

### Impact Explanation
Because step 2 always fails for such an amount, the PRC20 tokens already locked/burned on Push Chain for that outbound (or SPL/native funds queued in the Solana vault) become permanently stuck — this is a "permanent freezing of user- or protocol-controlled funds" outcome, reachable purely from an ordinary, unprivileged user submitting a large-value cross-chain transfer to a Solana destination (no malicious validator, relayer, or TSS participant required). This satisfies the in-scope impact criteria for the Push Chain gate (permanent freezing of funds via a broken invariant in the universal execution/outbound flow).

### Likelihood Explanation
Likelihood is credible but not certain to be triggered by default flows: it requires (a) an SVM-destination outbound, (b) a PRC20/asset amount ≥ 2^64 (~18.44 tokens at 18 decimals, or any amount at higher decimal precision), and (c) no upstream cap enforced elsewhere in the amount-validation pipeline for outbound creation. I did not find an explicit upper-bound check on `OutboundTx.Amount` in `x/uexecutor/keeper/create_outbound.go` or in registry/token-config validation that would prevent constructing such an outbound. However, I could not fully verify whether some other layer (e.g. PRC20 mint caps, chain config, or the `x/uregistry` token config) implicitly restricts amounts below `2^64` for SVM-bound assets, since exhaustive audit of that validation chain was not completed within available tool calls.

### Recommendation
- Add an explicit `amount.IsUint64()` check immediately after parsing `data.Amount` in the SVM signing-request builder (the same function containing lines 379-411), returning an error before ever calling `.Uint64()`, mirroring the guard already present in `BuildOutboundTransaction`.
- Ensure the check happens before TSS ever signs a message, not only at broadcast time, so a doomed outbound is rejected early (ideally at `BuildOutboundsFromReceipt`/outbound-creation time in `x/uexecutor`) rather than being TSS-signed and then stuck forever.
- Consider enforcing a canonical amount-fits-`uint64` (or chain-specific max) invariant centrally wherever `OutboundTx` is created for non-EVM destination chains, so this class of narrowing bug can't recur in future SVM/other non-EVM builders.

### Proof of Concept
1. A user bridges an 18-decimal PRC20-backed asset into Push Chain and triggers a `FUNDS`/`FUNDS_AND_PAYLOAD` outbound to a Solana destination with amount `20_000_000_000_000_000_000` (20 tokens, 18 decimals) — this exceeds `math.MaxUint64` (`18_446_744_073_709_551_615`).
2. `BuildOutboundsFromReceipt` stores `OutboundTx.Amount = "20000000000000000000"` with no bounds check.
3. The SVM signing-request builder parses this into a `*big.Int` and calls `amount.Uint64()` at line 381/399, silently truncating to `20_000_000_000_000_000_000 mod 2^64 = 1_553_255_926_290_448_384`. TSS validators sign a message committing to this wrong (truncated) amount.
4. When `BroadcastOutboundSigningRequest` → `BuildOutboundTransaction` re-parses the same `"20000000000000000000"` and calls `IsUint64()`, it now correctly errors with `"amount exceeds u64 max"` (line 705-707), so the transaction can never be broadcast.
5. The outbound is permanently stuck: TSS already produced a signature for a value the broadcast path will always reject, and no retry path changes the amount — the underlying funds are frozen indefinitely. [5](#0-4) [4](#0-3)

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

**File:** universalClient/chains/svm/tx_builder.go (L379-411)
```go
		switch instructionID {
		case 1: // Withdraw mode
			if amount.Uint64() == 0 {
				return nil, fmt.Errorf("withdraw mode: amount must be > 0")
			}
			if targetProgram == ([32]byte{}) {
				copy(targetProgram[:], recipientPubkey.Bytes())
			}

		case 2: // Execute mode
			if targetProgram == ([32]byte{}) {
				copy(targetProgram[:], recipientPubkey.Bytes())
			}
		}
	}

	// --- Construct the TSS message and hash it ---
	// This message is what TSS validators sign. The gateway contract reconstructs
	// the same message on-chain and verifies the signature matches.
	messageHash, err := tb.constructTSSMessage(
		instructionID, chainID, data.SigningDeadline, amount.Uint64(),
		txID, universalTxID, sender, token, gasFee,
		targetProgram, accounts, ixData,
		revertRecipient, revertMint, revertMsg,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to construct TSS message: %w", err)
	}

	return &common.UnsignedSigningReq{
		SigningHash: messageHash, // This is the keccak256 hash to be signed by TSS
		Nonce:       nonce,
	}, nil
```

**File:** universalClient/chains/svm/tx_builder.go (L700-707)
```go
	amount := new(big.Int)
	amount, ok := amount.SetString(data.Amount, 10)
	if !ok {
		return nil, 0, fmt.Errorf("invalid amount: %s", data.Amount)
	}
	if !amount.IsUint64() {
		return nil, 0, fmt.Errorf("amount exceeds u64 max: %s", data.Amount)
	}
```
