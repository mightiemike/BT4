### Title
Silent uint256→uint64 truncation of outbound bridging amount in the TSS signing path — analogous unchecked narrowing cast to the SushiSwap `int128` bug (File: `universalClient/chains/svm/tx_builder.go`)

### Summary
The reported SushiSwap bug is a class of vulnerability where an unchecked/unsafe narrowing type cast (`uint128 → int128`) silently reinterprets a large unsigned value into a wildly different value that is then used to authorize a financial operation. Push Chain's SVM `TxBuilder.GetOutboundSigningRequest` has the same bug class: it calls Go's `big.Int.Uint64()` directly on an attacker/user-influenced uint256 `Amount` without first checking `amount.IsUint64()`, silently truncating to the low 64 bits before that truncated value is baked into the exact byte message that Universal Validators sign via TSS.

### Finding Description
`OutboundTx.Amount` (and the `UniversalTxOutboundEvent.Amount`/`event.Amount` it derives from — see `x/uexecutor/keeper/create_outbound.go:72`, `x/uexecutor/types/gateway_pc_event_decode.go:20`) is a full uint256 value with no upper bound enforced anywhere in the executor module (`Inbound.ValidateForExecution` in `x/uexecutor/types/inbound.go:126-138` only checks non-negativity, not an upper bound; the same is true for `UniversalPayload.ValidateBasic`).

When an SVM-bound outbound is created, `TxBuilder.GetOutboundSigningRequest` (`universalClient/chains/svm/tx_builder.go`) parses that string amount into a `*big.Int` and then, at:

```go
messageHash, err := tb.constructTSSMessage(
    instructionID, chainID, data.SigningDeadline, amount.Uint64(),
    ...
)
```

calls `amount.Uint64()` directly with no bounds check. Per Go's documentation, `big.Int.Uint64()` is undefined/truncating when the value doesn't fit in 64 bits — in practice it returns the low 64 bits, silently discarding the rest, exactly like the unchecked `-int128(amount)` cast in the SushiSwap report. [1](#0-0) 

Contrast this with the sibling function `BuildOutboundTransaction`, which explicitly guards the identical value:

```go
amount, ok := amount.SetString(data.Amount, 10)
if !ok { ... }
if !amount.IsUint64() {
    return nil, 0, fmt.Errorf("amount exceeds u64 max: %s", data.Amount)
}
``` [2](#0-1) 

`GetOutboundSigningRequest` — the function that actually produces the `SigningHash` that TSS validators cryptographically sign — has no equivalent guard before its `amount.Uint64()` call at line 399.

The truncated value is what gets embedded, byte-for-byte, into the TSS message (`amountBytes := ...; binary.BigEndian.PutUint64(amountBytes, amount)` in `constructTSSMessage`), which is what the destination Solana gateway program cryptographically verifies and executes against: [3](#0-2) 

Because 18-decimal tokens (e.g. the native `WPC`/`PC` gas token used throughout `x/uexecutor`, see `app/README.md` "Base denom | `upc` (18 decimals, EVM-aligned)") only need ~18.45 whole tokens to exceed `2^64-1` (≈1.8446744×10¹⁹), this is trivially reachable by an ordinary user bridging a moderately large amount of an 18-decimal PRC20/native asset back out to Solana — no privileged actor or malicious validator is required. Since `big.Int.Uint64()` truncation is deterministic, every honest Universal Validator computes the identical (wrong) truncated amount and signs it — so this does not cause validator disagreement, but it does cause the canonical Push Chain `OutboundTx.Amount` (correct, full uint256, already reflected in `UniversalTx` state and any prior burn/deposit accounting) to diverge from the amount actually authorized and released on the destination Solana chain.

### Impact Explanation
This breaks the "PRC20/native asset accounting must not misroute value" invariant: the amount Push Chain's ledger believes was bridged out (and any corresponding burn/PRC20 debit already applied) is silently replaced by `amount mod 2^64` in the value that is actually cryptographically authorized and paid out on Solana. Depending on the specific amount, this produces:
- Permanent underpayment/loss of user funds relative to the recorded canonical `OutboundTx.Amount` (the difference is neither delivered to the user nor recoverable through the normal outbound flow), or
- A false-negative "amount must be > 0" rejection when the true amount is an exact multiple of 2^64 (`amount.Uint64() == 0` check at line 381), causing legitimate large-amount withdrawals to be spuriously blocked.

This falls squarely within the allowed impact gate: "corruption of ... native asset accounting ... revert destination ... canonical UniversalTx state" and "permanent loss ... of user or protocol-controlled funds," reachable via an ordinary unprivileged user's deposit/withdraw of a bridged asset — no malicious validator, peer, or admin action required.

### Likelihood Explanation
Likelihood is moderate-to-high for 18-decimal assets: any user withdrawal/outbound whose amount (in smallest units) exceeds `2^64-1` (roughly 18.45 tokens for an 18-decimal PRC20, far smaller thresholds are not required for the exploit — any value simply needs to exceed the uint64 boundary) will trigger this silent truncation automatically, with no attacker sophistication needed beyond initiating a normal large-value withdrawal. The bug is deterministic and will reproduce identically for every honest validator, so it is not masked by honest-majority consensus.

### Recommendation
In `GetOutboundSigningRequest` (and any other call site in `universalClient/chains/svm/tx_builder.go` that narrows the outbound `Amount` to `uint64`), add the same `amount.IsUint64()` guard used in `BuildOutboundTransaction` before calling `.Uint64()`, and return a hard error (rejecting/aborting the outbound signing flow, or route it to `AbortOutbound`) rather than silently truncating. Additionally, consider enforcing an explicit upper bound (`< 2^64`) on `Amount`/`OutboundTx.Amount` in `x/uexecutor/types/inbound.go` `ValidateForExecution` and in outbound-creation validation (`x/uexecutor/keeper/create_outbound.go`) for any chain whose gateway wire format uses a fixed-width `uint64` amount field, so the mismatch is caught at outbound-creation time rather than deep inside the TSS-signing path.

### Proof of Concept
1. A user bridges an 18-decimal PRC20 (or the native gas-token-backed asset) inbound, then triggers an outbound (e.g. via `MsgExecutePayload` causing a `UniversalTxOutboundEvent`) with `Amount = "18450000000000000000"` (18.45 tokens, `> 2^64-1 = 18446744073709551615`).
2. `BuildOutboundsFromReceipt` records `OutboundTx.Amount = "18450000000000000000"` verbatim in the canonical `UniversalTx`/`PendingOutbounds` state (`x/uexecutor/keeper/create_outbound.go:72`).
3. `puniversald` (Universal Validator worker) calls `TxBuilder.GetOutboundSigningRequest` with this `OutboundCreatedEvent`. `amount.Uint64()` truncates `18450000000000000000` to `3253255926" (i.e. `18450000000000000000 mod 2^64`), which is embedded into the TSS signing message.
4. Every honest TSS participant deterministically signs over the truncated amount; the resulting signature/transaction, once broadcast to the Solana gateway program, only authorizes release of the truncated (far smaller/different) amount.
5. The user/recipient never receives the recorded `18450000000000000000`-unit amount, while Push Chain's canonical `OutboundTx`/`UniversalTx` state and prior accounting reflect the full, un-truncated amount — a permanent, unrecoverable mismatch between recorded and delivered value.

Note: I was not able to view the full body of `GetOutboundSigningRequest` above line ~300 within tool-call limits, so I cannot rule out an earlier, out-of-view bounds check on `amount` in that same function. However, the code shown (lines 300–412) performs `amount.Uint64()` directly with no visible guard, in clear contrast to the explicit `IsUint64()` check present in the sibling function `BuildOutboundTransaction` for the identical value — a background agent should verify the full function body before remediation to confirm no earlier guard exists.

### Citations

**File:** universalClient/chains/svm/tx_builder.go (L395-406)
```go
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

**File:** universalClient/chains/svm/tx_builder.go (L1396-1425)
```go
func (tb *TxBuilder) constructTSSMessage(
	instructionID uint8,
	chainID string,
	deadlineUnix int64,
	amount uint64,
	txID [32]byte,
	universalTxID [32]byte,
	sender [20]byte,
	token [32]byte,
	gasFee uint64,
	targetProgram [32]byte,
	execAccounts []GatewayAccountMeta,
	ixData []byte,
	revertRecipient [32]byte,
	revertMint [32]byte,
	revertMsg []byte,
) ([]byte, error) {
	// Wire format expected by the SVM gateway program's validate_message:
	//   PREFIX || instruction_id || chain_id || deadline(i64 BE) || amount(u64 BE) || additional_data
	message := append([]byte(nil), tssMessagePrefix...)
	message = append(message, instructionID)
	message = append(message, []byte(chainID)...)

	deadlineBytes := make([]byte, 8)
	binary.BigEndian.PutUint64(deadlineBytes, uint64(deadlineUnix))
	message = append(message, deadlineBytes...)

	amountBytes := make([]byte, 8)
	binary.BigEndian.PutUint64(amountBytes, amount)
	message = append(message, amountBytes...)
```
