## Verdict: Valid vulnerability

### Title
Universal Client SVM event parser trusts any log line matching the gateway discriminator without verifying it was emitted by the actual gateway program invocation, letting an unprivileged attacker forge inbound events that honest Universal Validators converge on — ([File: universalClient/chains/svm/event_listener.go], [File: universalClient/chains/svm/event_parser.go])

### Summary
`GetSignaturesForAddress` returns *any* transaction signature that references the gateway pubkey in its account list [1](#0-0) , not only transactions where the gateway program was actually the invoking program for a given instruction. `processSignatureBatch` then iterates over **every** log line in `tx.Meta.LogMessages` for that transaction and calls `determineEventType`/`ParseEvent` on each one independent of which program actually emitted it [2](#0-1) . `determineEventType` only checks whether the first 8 bytes of a base64-decoded `Program data:` line match a known Anchor event discriminator [3](#0-2) , and `decodeUniversalTxEvent`/`parseSendFundsEvent` then blindly decode the remaining bytes as Borsh fields (sender, recipient, token, amount, payload, revert recipient, tx type) with no cross-check against the actual invoking program ID or instruction context [4](#0-3) .

### Finding Description
Anchor event discriminators (`sighash("event:<Name>")`) are public and deterministically computable by anyone; they are not a secret. `sol_log_data` (the Solana syscall backing `Program data: ...` log lines) can be invoked by any program running under a transaction, and inclusion of an account (like the gateway program's pubkey) in a transaction's account list requires no permission from that account. This means an unprivileged attacker can:

1. Deploy or use any arbitrary program.
2. Build a transaction that (a) references the gateway program's pubkey as an extra account somewhere in its instructions (so `GetSignaturesForAddress(gatewayAddr)` returns this signature), and (b) has one of its own instructions call `sol_log_data` with a payload whose first 8 bytes match the `send_funds` discriminator, followed by attacker-chosen bytes matching the expected Borsh layout (`sender`, `recipient` (20-byte target), `bridge_token`, `bridge_amount`, optional payload, `revert_recipient`, `tx_type`, etc.).

The `universalClient` code path never verifies that this log line was emitted from *within* an actual invocation of the real gateway program (e.g., by tracking the `Program <id> invoke [depth]` / `Program <id> success` nesting in `LogMessages` and confirming the emitting program equals the configured gateway address). It only checks the discriminator bytes and that the log starts with `"Program data: "`.

Because this parsing logic is identical, deterministic client code run by every Universal Validator, all honest UVs polling the same Solana RPC data would independently derive the same forged `Inbound` payload from the same attacker transaction, compute the same `ballotID = hex(marshal(Inbound))` [5](#0-4) , and submit `MsgVoteInbound` for it via `VoteInbound`/`VoteOnInboundBallot` [6](#0-5) . No malicious validator, relayer, or TSS participant is needed — the forgery happens purely in the shared client parsing logic triggered by an ordinary, unprivileged external-chain transaction.

### Impact Explanation
Once quorum of honest UVs vote the forged inbound, `VoteInbound` finalizes and Push Chain executes the inbound — minting a PRC20/native synthetic representation to an attacker-chosen recipient for an attacker-chosen token/amount, and potentially running an attacker-chosen payload (`GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD`), per the documented inbound execution flow [7](#0-6) . This is an unauthorized mint / accounting corruption with no real funds ever custodied on the Solana gateway program — a direct material fund-creation/theft vulnerability reachable by any unprivileged attacker who can submit ordinary Solana transactions, satisfying the "unauthorized mint" and "forged ... inbound ... state accepted through user-reachable flows with honest validators and honest nodes" allowed-impact categories.

### Likelihood Explanation
High. No privileged access, validator collusion, or key compromise is required. Anchor event discriminators are computable offline; constructing a transaction that references an arbitrary account and independently emits `sol_log_data` from the attacker's own program requires only basic Solana program development — well within reach of an unprivileged actor.

### Recommendation
When scanning `tx.Meta.LogMessages`, bind each `Program data: ...` line to the actual invoking program by tracking Solana's `Program <id> invoke [depth]` / `Program <id> success|failed` nesting and only accept discriminator matches whose enclosing invocation frame's program ID equals the configured `gatewayAddress`. Alternatively/additionally, cross-check against the transaction's parsed instructions (`tx.Transaction.Message.Instructions`) to confirm the exact instruction that produced the log was executed by the gateway program ID, rather than trusting the account-inclusion match from `GetSignaturesForAddress` plus a bare byte-prefix match.

### Proof of Concept
A Go test can demonstrate the gap directly against `ParseEvent`/`determineEventType`: feed a `solanarpc.GetTransactionResult` whose `Meta.LogMessages` contains a `Program data: ...` line with the `send_funds` discriminator followed by attacker-controlled Borsh bytes (arbitrary sender/recipient/token/amount), while the transaction's actual `Message.Instructions`/invoke-log framing shows the enclosing program ID is *not* the configured gateway address. The test would assert that `event_listener.go`'s `processSignatureBatch` → `determineEventType` → `ParseEvent` → `parseSendFundsEvent`/`decodeUniversalTxEvent` still produces a non-nil `store.Event` with `EventData` reflecting the attacker-chosen fields, proving the code path never checks the invoking program ID/instruction context before accepting the log as authoritative.

### Citations

**File:** universalClient/chains/svm/rpc_client.go (L299-311)
```go
func (rc *RPCClient) GetSignaturesForAddress(ctx context.Context, address solana.PublicKey, before solana.Signature) ([]*rpc.TransactionSignature, error) {
	var opts *rpc.GetSignaturesForAddressOpts
	if !before.IsZero() {
		opts = &rpc.GetSignaturesForAddressOpts{Before: before}
	}
	var signatures []*rpc.TransactionSignature
	err := rc.executeWithFailover(ctx, "get_signatures_for_address", func(client *rpc.Client) error {
		var innerErr error
		signatures, innerErr = client.GetSignaturesForAddressWithOpts(ctx, address, opts)
		return innerErr
	})
	return signatures, err
}
```

**File:** universalClient/chains/svm/event_listener.go (L298-309)
```go
		// Process each log in the transaction
		if tx != nil && tx.Meta != nil && len(tx.Meta.LogMessages) > 0 {
			for logIndex, log := range tx.Meta.LogMessages {
				// Determine event type based on discriminator
				eventType := el.determineEventType(log)
				if eventType == "" {
					continue
				}

				// Parse gateway event from individual log
				event := ParseEvent(log, sig.Signature.String(), sig.Slot, uint(logIndex), eventType, el.chainID, el.logger)
				if event != nil {
```

**File:** universalClient/chains/svm/event_listener.go (L398-423)
```go
// determineEventType determines the event type based on the log discriminator
func (el *EventListener) determineEventType(log string) string {
	if !strings.HasPrefix(log, "Program data: ") {
		return ""
	}

	eventData := strings.TrimPrefix(log, "Program data: ")
	decoded, err := base64.StdEncoding.DecodeString(eventData)
	if err != nil {
		return ""
	}

	if len(decoded) < 8 {
		return ""
	}

	discriminator := strings.ToLower(hex.EncodeToString(decoded[:8]))

	// Look up event type from discriminator map
	eventType, ok := el.discriminatorToEventType[discriminator]
	if !ok {
		return ""
	}

	return eventType
}
```

**File:** universalClient/chains/svm/event_parser.go (L236-284)
```go
// decodeUniversalTxEvent decodes a TxWithFunds event
func decodeUniversalTxEvent(data []byte, logger zerolog.Logger) (*common.UniversalTx, error) {
	if len(data) < 120 {
		logger.Warn().
			Int("data_len", len(data)).
			Msg("data might be too short for complete TxWithFunds event")
	}

	offset := 8
	payload := &common.UniversalTx{}

	// Parse sender (32 bytes)
	if len(data) < offset+32 {
		return nil, fmt.Errorf("not enough data for sender")
	}
	sender := solana.PublicKey(data[offset : offset+32])
	// Convert sender to hex format
	senderHex, err := base58ToHex(sender.String())
	if err != nil {
		logger.Warn().Err(err).Msg("failed to convert sender to hex, using base58")
		payload.Sender = sender.String()
	} else {
		payload.Sender = senderHex
	}
	offset += 32

	// Parse recipient (20 bytes - byte20 format)
	if len(data) < offset+20 {
		return nil, fmt.Errorf("not enough data for recipient")
	}
	// Convert 20 bytes to hex string (0x + 40 hex chars)
	recipientBytes := data[offset : offset+20]
	payload.Recipient = "0x" + hex.EncodeToString(recipientBytes)
	offset += 20

	// Parse bridge_token (32 bytes)
	if len(data) < offset+32 {
		return nil, fmt.Errorf("not enough data for bridge_token")
	}
	bridgeToken := solana.PublicKey(data[offset : offset+32])
	payload.Token = bridgeToken.String()
	offset += 32

	// Parse bridge_amount (8 bytes)
	if len(data) < offset+8 {
		return nil, fmt.Errorf("not enough data for bridge_amount")
	}
	bridgeAmount := binary.LittleEndian.Uint64(data[offset : offset+8])
	payload.Amount = fmt.Sprintf("%d", bridgeAmount)
```

**File:** x/uexecutor/keeper/inbound.go (L14-21)
```go
// RecordInboundVote idempotently records a validator's vote on an inbound by
// appending to the per-utx PendingInbounds entry. Creates the entry on the
// first vote for a given utx_key, creates a new variant on the first vote
// of a given (inbound payload bytes / ballotID), and appends the voter to
// an existing variant on subsequent votes for the same payload (deduped).
//
// utx_key = sha256(source_chain:tx_hash:log_index) — see GetInboundUniversalTxKey.
// ballotID = hex(marshal(Inbound)) — see GetInboundBallotKey.
```

**File:** x/uexecutor/keeper/voting.go (L11-58)
```go
func (k Keeper) VoteOnInboundBallot(
	ctx context.Context,
	universalValidator sdk.ValAddress,
	inbound types.Inbound,
) (isFinalized bool,
	isNew bool,
	err error) {
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return false, false, err
	}

	universalValidatorSet, err := k.uvalidatorKeeper.GetEligibleVoters(ctx)
	if err != nil {
		return false, false, err
	}

	// number of validators
	totalValidators := len(universalValidatorSet)

	// votesNeeded = ceil(2/3 * totalValidators)
	// >2/3 quorum similar to tendermint
	votesNeeded := (types.VotesThresholdNumerator*totalValidators)/types.VotesThresholdDenominator + 1

	k.Logger().Debug("voting on inbound ballot",
		"ballot_key", ballotKey,
		"validator", universalValidator.String(),
		"total_validators", totalValidators,
		"votes_needed", votesNeeded,
	)

	// Convert []sdk.ValAddress → []string
	universalValidatorSetStrs := make([]string, len(universalValidatorSet))
	for i, v := range universalValidatorSet {
		universalValidatorSetStrs[i] = v.IdentifyInfo.CoreValidatorAddress
	}

	// Step 2: Call VoteOnBallot for this inbound synthetic
	_, isFinalized, isNew, err = k.uvalidatorKeeper.VoteOnBallot(
		ctx,
		ballotKey,
		uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		universalValidator.String(),
		uvalidatortypes.VoteResult_VOTE_RESULT_SUCCESS,
		universalValidatorSetStrs,
		int64(votesNeeded),
		int64(types.DefaultExpiryAfterBlocks),
	)
```

**File:** x/uexecutor/README.md (L163-179)
```markdown
### Lifecycle Walkthrough

A typical `FUNDS_AND_PAYLOAD` inbound, end to end:

```
1. UV observes a source-chain gateway event.
2. UV submits MsgVoteInbound. The UTX is created the moment the first vote
   arrives, with id = sha256(sourceChain:txHash:logIndex). Only the
   InboundTx field is populated; PcTx and OutboundTx are empty.
   (UTX id is also added to PendingInbounds.)

3. Threshold of UV votes reached. The keeper executes the inbound:
   a. Mints the PRC20 to the recipient's UEA address.
      A new PCTx (deposit) is appended to UTX.PcTx.
   b. Runs the universal payload through the UEA.
      A second PCTx (executeUniversalTx) is appended.
   (UTX id removed from PendingInbounds.)
```
