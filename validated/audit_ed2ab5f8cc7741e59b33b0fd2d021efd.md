Confirmed: for the outbound path built in `BuildOutboundsFromReceipt` (x/uexecutor/keeper/create_outbound.go:69-91), `RevertInstructions.FundRecipient` is set to `event.RevertRecipient` — a raw string decoded from the `UniversalTxOutbound` EVM event log emitted by `UniversalGatewayPC`. This field is **user/attacker-controlled at the time the withdrawal is initiated** (it comes from Solidity contract call parameters, not from any registry or validator vote), and it is never format-validated as an EVM address before being consumed downstream in `x/uexecutor/keeper/outbound.go`.

### Title
Unvalidated `RevertInstructions.FundRecipient` causes attacker-triggerable permanent freezing of re-minted PRC20 refunds on failed outbound - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
When a Push Chain outbound (withdrawal to an external chain) fails, `handleFailedOutbound` and `applyGasRefund` re-mint the bridged PRC20 / refund excess gas back on Push Chain to `outbound.RevertInstructions.FundRecipient`, converting it directly with `common.HexToAddress(recipient)` [1](#0-0) [2](#0-1) . That field is populated verbatim from `event.RevertRecipient`, a value decoded out of the `UniversalTxOutbound` log emitted by the user's own withdrawal call [3](#0-2) . Unlike the `Inbound.RevertInstructions.FundRecipient` path, which is passed through `LenientCanonicalizeAddress` per source-chain namespace [4](#0-3) , this outbound-side value has **no equivalent canonicalization or validation step** anywhere between event decoding and `common.HexToAddress`.

### Finding Description
`go-ethereum`'s `common.HexToAddress` never errors on malformed input: non-hex-decodable strings degrade silently (bytes derived via `FromHex`, missing/invalid bytes treated as zero), and any string shorter/longer than 20 bytes is right- or left-truncated/padded deterministically. This means an ordinary user submitting a withdrawal through the gateway contract can supply an arbitrary `revertRecipient` value — not necessarily a real address they control — that decodes into a Push Chain EVM address nobody holds a key for (e.g. all-zero, or an unrelated PRC20/system contract address), while the legitimate `Sender` field on the same outbound is a valid address they DO control.

Because `handleFailedOutbound` prefers `RevertInstructions.FundRecipient` over `outbound.Sender` whenever it's non-empty [5](#0-4) , and `applyGasRefund` does the same for the excess-gas refund path [2](#0-1) , this is the same class of bug as the audited report: the destination "account" for the deposited/re-minted value is accepted from user input and used to move funds without being bound/validated to a real, controllable destination — funds land somewhere unusable rather than being blocked or defaulted safely.

### Impact Explanation
If the destination-chain outbound legitimately fails (external chain reorg, gas underpricing, relay failure, etc. — none of which requires a malicious validator), the protocol re-mints the bridged PRC20 to `FundRecipient` on Push Chain. If that value was garbage/malformed at withdrawal time, the re-minted tokens (and any excess gas refund) are minted to an address the original depositor cannot access — a permanent freeze of user funds triggered entirely by the depositor's own (possibly accidental, possibly malicious/self-harming, but also potentially exploitable to grief a specific address or push funds to a colliding address) input, with no additional validation catching it before the mint executes.

### Likelihood Explanation
This requires only an ordinary user submitting a normal cross-chain withdrawal with a crafted/malformed `revertRecipient` parameter to the `UniversalGatewayPC` contract, and later having (or contriving via griefing, e.g. underfunding gas so the destination call fails) the outbound observed as failed by honest validators — no privileged or malicious-validator behavior needed. Likelihood is moderate: it depends on the outbound actually failing, which normally happens due to external factors, but a user can deliberately structure their transaction (e.g., minimal gas limit) to make failure likely and then benefit from or exploit knowing the corrupted destination.

### Recommendation
Validate `RevertInstructions.FundRecipient` (i.e. `event.RevertRecipient`) as a well-formed EVM address (`ethcommon.IsHexAddress`) at outbound-creation time in `BuildOutboundsFromReceipt`, and again defensively before use in `handleFailedOutbound`/`applyGasRefund`. If invalid, fall back to `outbound.Sender` (as already done when the field is empty) instead of silently truncating/padding through `common.HexToAddress`.

### Proof of Concept
1. User calls the Push Chain gateway's withdrawal function, initiating an outbound whose `revertRecipient` parameter is a non-hex or wrong-length string (e.g. `"zz"` or a 10-byte value), which is legal at the EVM ABI/contract level if the field is a raw `bytes`/`string` rather than a `checksummed address` type.
2. `BuildOutboundsFromReceipt` copies this raw value into `outbound.RevertInstructions.FundRecipient` unvalidated.
3. The outbound is broadcast to the external chain and, due to normal external-chain conditions (e.g. insufficient gas at destination), is observed by honest Universal Validators as failed via `MsgVoteOutbound`.
4. `handleFailedOutbound` executes `common.HexToAddress(recipient)` on the malformed value and calls `CallPRC20Deposit`, minting the bridged PRC20 amount to a deterministic-but-uncontrolled address, permanently locking those funds away from the depositor. [6](#0-5) 

Note: I was not able to fully trace the Solidity-side `UniversalGatewayPC.sol` contract (not indexed in this scope) to confirm whether `revertRecipient` is declared as a strict `address` type at the ABI level, which would make on-chain rejection of malformed values automatic instead of requiring this Go-side fix. If the Solidity ABI enforces `address` typing, this specific vector is not exploitable and the finding should be downgraded to "reject — existing guard preserves invariant." A Devin session with access to the `core-contracts`/Solidity repo would be needed to confirm this before treating the report as final.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L107-119)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L201-206)
```go
	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
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

**File:** x/uexecutor/types/inbound.go (L21-35)
```go
func (p *Inbound) Canonicalize() {
	p.SourceChain = strings.TrimSpace(p.SourceChain)
	p.TxHash = utils.LenientCanonicalizeTxHash(p.SourceChain, p.TxHash)
	p.Sender = utils.LenientCanonicalizeAddress(p.SourceChain, p.Sender)
	p.AssetAddr = utils.LenientCanonicalizeAddress(p.SourceChain, p.AssetAddr)
	// Recipient lives on Push Chain (EVM) regardless of source chain.
	p.Recipient = utils.LenientCanonicalizeEVMAddress(p.Recipient)
	p.LogIndex = strings.TrimSpace(p.LogIndex)
	p.Amount = strings.TrimSpace(p.Amount)
	p.RawPayload = utils.CanonicalizeHexBlob(p.RawPayload)
	p.VerificationData = utils.CanonicalizeHexBlob(p.VerificationData)
	if p.RevertInstructions != nil {
		// Refunds return to the source chain.
		p.RevertInstructions.FundRecipient = utils.LenientCanonicalizeAddress(p.SourceChain, p.RevertInstructions.FundRecipient)
	}
```
