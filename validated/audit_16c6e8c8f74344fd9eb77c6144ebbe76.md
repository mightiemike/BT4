### Title
Unbounded `PendingInbounds.Walk` full-table scan on every inbound ballot terminal transition enables gas-amplification DoS from cheap, attacker-controlled source-chain deposits - ([File: x/uexecutor/keeper/ballot_hooks.go])

### Summary
`afterInboundBallotTerminal` locates the `PendingInbounds` entry owning a terminated ballot by doing a full unbounded `Walk` over the entire `PendingInbounds` collection [1](#0-0) . This mirrors the Opyn Crab Netting pattern: a queue/index that must be linearly scanned/skipped during processing, whose size is controlled by an unprivileged actor via cheap, low-value operations, and which is read on the hot consensus path for every unrelated terminal event.

### Finding Description
Every source-chain deposit event (an "inbound") that receives at least one validator vote creates or updates a `PendingInboundEntry` keyed by `utx_key` in the `PendingInbounds` collection [2](#0-1) . There is no minimum amount, dust threshold, or fee-based rate limiting on inbound creation — zero-amount inbounds are explicitly valid and processed [3](#0-2) , and ballot expiry is effectively disabled (`DefaultExpiryAfterBlocks = 100_000_000`, "effectively disable expiry" per the comment) [4](#0-3) , so pending entries persist indefinitely until finalized.

An external, unprivileged attacker who controls a source chain account can emit an unbounded number of cheap (even zero-value) gateway deposit events on a low-fee external chain (e.g. Solana). Once observed and voted on by even a single honest Universal Validator, each event creates a distinct `PendingInboundEntry` in the on-chain `PendingInbounds` collection — entirely driven by external, attacker-controlled activity, with no privileged actor or malicious validator required.

`afterInboundBallotTerminal` is invoked by `x/uvalidator`'s `MarkBallotFinalized`/`MarkBallotExpired` for every INBOUND_TX ballot that reaches a terminal state [5](#0-4) , which happens inside ordinary `MsgVoteInbound` processing whenever quorum is reached [6](#0-5) . Each such terminal transition triggers a full linear `Walk` over **every** entry currently in `PendingInbounds`, inspecting each entry's `Variants` list looking for a matching `BallotId`, with no early bound or pagination [7](#0-6) . The code comment itself assumes "the pending set is small and transient," an assumption the attacker can invalidate.

Because this scan runs once per terminal ballot and the number of pending entries scales with the number of attacker-injected cheap deposits, the aggregate cost across all in-flight ballots resolving in the same window grows roughly quadratically (each of N terminal events scans an average O(N) live pending entries), directly analogous to the Crab Netting bug where each netting pass had to skip an attacker-inflated number of blank queue entries.

### Impact Explanation
This imposes unbounded, attacker-controlled computational cost on the consensus-critical inbound-voting path executed by every honest validating node. As the flood of cheap inbounds inflates `PendingInbounds`, ordinary, honest inbound processing (quorum finalization for legitimate user deposits) becomes progressively slower for every node, since each terminal transition re-scans the whole growing set. In the worst case this can meaningfully degrade block processing time/gas budgets for the `x/uexecutor` module and delay/degrade finalization of legitimate deposits — a non-network-level, state-machine-reachable DoS vector satisfying the "denial of service... reachable without privileged control" allowed-impact criterion.

### Likelihood Explanation
Medium. Exploitation requires no privileged access — an attacker only needs to submit many cheap/zero-value gateway deposit transactions on any enabled, low-fee external chain and wait for at least one honest validator to observe/vote on each. It does not require compromising or colluding with validators (a single honest vote already inserts the pending entry). The main constraint is real-world cost of emitting many distinct source-chain events, which is negligible on chains with sub-cent fees (e.g. Solana), and the effect compounds over time since there is no expiry pruning in practice (100M-block expiry) and no per-address/entry rate limiting in `x/uexecutor`.

### Recommendation
Replace the full `PendingInbounds.Walk` lookup with a direct indexed lookup: maintain a secondary index mapping `ballotID -> utxKey` (set when the variant/ballot is created in `RecordInboundVote`, removed when the entry is cleared) so `afterInboundBallotTerminal` can do an O(1) `Get` instead of an O(N) scan. Additionally, consider adding a minimum-inbound-value or per-source-account rate limit, and enabling a bounded, enforced ballot expiry (rather than the effectively-disabled 100M block default) so stale/spam entries are pruned rather than accumulating without bound — mirroring the accepted Opyn fix pattern of allowing the queue to be trimmed/indexed rather than requiring full-list scans.

### Proof of Concept
1. Attacker registers/controls a source-chain account on a low-fee, inbound-enabled chain (per `x/uregistry` chain config).
2. Attacker submits N (e.g. 50,000+) cheap or zero-amount gateway deposit transactions in rapid succession, each with a unique `tx_hash`/`log_index`.
3. At least one honest Universal Validator observes and votes each event via `MsgVoteInbound`, calling `RecordInboundVote`, which inserts N distinct `PendingInboundEntry` records into `PendingInbounds` [2](#0-1) .
4. As quorum is subsequently reached (or ballots eventually terminate) for any inbound — attacker's or a legitimate user's — `MarkBallotFinalized`/`MarkBallotExpired` fires `AfterBallotTerminal`, invoking `afterInboundBallotTerminal`, which performs a full `Walk` across all N pending entries [1](#0-0) .
5. Repeating this for many concurrently-resolving ballots multiplies the total scan work across the block, degrading processing time for all honest nodes and delaying finalization of legitimate, unrelated user deposits — without any validator or admin misbehavior.

Note: I was unable to fully verify the exact per-block gas/time metering applied to this specific keeper call path (whether it is separately gas-metered like EVM opcodes or only bounded by the overall Cosmos SDK block gas limit for `MsgVoteInbound`), which affects how severe the practical DoS threshold is; a Devin session with the full test/benchmark suite would be needed to quantify the exact entry count required to cause material block-time degradation.

### Citations

**File:** x/uexecutor/keeper/ballot_hooks.go (L76-97)
```go
) error {
	// Ballot IDs are one-way canonical digests (not reversible), so locate
	// the owning audit-trail entry by scanning PendingInbounds for the
	// variant carrying this ballot ID. The pending set is small and
	// transient, and this hook only fires on terminal transitions.
	var (
		utxKey string
		entry  types.PendingInboundEntry
		found  bool
	)
	err := h.k.PendingInbounds.Walk(ctx, nil, func(key string, e types.PendingInboundEntry) (bool, error) {
		for _, v := range e.Variants {
			if v.BallotId == ballotID {
				utxKey, entry, found = key, e, true
				return true, nil
			}
		}
		return false, nil
	})
	if err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/inbound.go (L27-46)
```go
func (k Keeper) RecordInboundVote(
	ctx context.Context,
	inbound types.Inbound,
	voter string,
	ballotID string,
) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	height := uint64(sdkCtx.BlockHeight())
	utxKey := types.GetInboundUniversalTxKey(inbound)

	entry, err := k.PendingInbounds.Get(ctx, utxKey)
	if err != nil && !errors.Is(err, collections.ErrNotFound) {
		return err
	}
	if errors.Is(err, collections.ErrNotFound) {
		entry = types.PendingInboundEntry{
			UtxKey:          utxKey,
			CreatedAtHeight: height,
		}
	}
```

**File:** test/integration/uexecutor/inbound_zero_amount_test.go (L115-144)
```go
func TestInboundZeroAmountFundsAndPayload(t *testing.T) {
	t.Run("zero amount FUNDS_AND_PAYLOAD skips deposit and executes payload", func(t *testing.T) {
		chainApp, ctx, vals, coreVals, ueaAddrHex := setupZeroAmountInboundTest(t, 4)
		usdcAddress := utils.GetDefaultAddresses().ExternalUSDCAddr
		testAddress := utils.GetDefaultAddresses().DefaultTestAddr

		validUP := &uexecutortypes.UniversalPayload{
			To:                   ueaAddrHex.String(),
			Value:                "0",
			Data:                 "0xa9059cbb000000000000000000000000527f3692f5c53cfa83f7689885995606f93b616400000000000000000000000000000000000000000000000000000000000f4240",
			GasLimit:             "21000000",
			MaxFeePerGas:         "1000000000",
			MaxPriorityFeePerGas: "200000000",
			Nonce:                "1",
			Deadline:             "9999999999",
			VType:                uexecutortypes.VerificationType(1),
		}

		inbound := &uexecutortypes.Inbound{
			SourceChain:      "eip155:11155111",
			TxHash:           "0xzeroamt01",
			Sender:           testAddress,
			Recipient:        "",
			Amount:           "0",
			AssetAddr:        usdcAddress.String(),
			LogIndex:         "1",
			TxType:           uexecutortypes.TxType_FUNDS_AND_PAYLOAD,
			UniversalPayload: validUP,
			VerificationData: "",
		}
```

**File:** x/uexecutor/types/constants.go (L39-49)
```go
// Quorum numerator/denominator for validator votes (>2/3)
const (
	VotesThresholdNumerator   = 2
	VotesThresholdDenominator = 3

	// Default number of blocks after which ballot expires.
	// Set to 100M (~19 years at 6s blocks) to effectively disable expiry.
	// Ballots should not expire without an escape hatch for stuck pending items.
	// Disabling the expiry temporarily, will most likely enable once ballot pruning is implemented or escape hatch
	DefaultExpiryAfterBlocks = 100_000_000
)
```

**File:** x/uvalidator/keeper/ballot.go (L155-189)
```go
// updated before the canonical ballot record is rewritten with its final status.
//
// Fires the BallotHooks terminal callback (if registered) AFTER all writes
// have committed. Hook errors are logged but do NOT block the terminal
// transition.
func (k Keeper) MarkBallotFinalized(ctx context.Context, id string, status types.BallotStatus) error {
	if status != types.BallotStatus_BALLOT_STATUS_PASSED && status != types.BallotStatus_BALLOT_STATUS_REJECTED {
		return fmt.Errorf("invalid finalization status: %v", status)
	}

	ballot, err := k.Ballots.Get(ctx, id)
	if err != nil {
		return err
	}

	k.Logger().Debug("marking ballot as finalized",
		"ballot_id", id,
		"final_status", status.String(),
	)

	if err := k.ActiveBallotIDs.Remove(ctx, id); err != nil {
		return err
	}
	if err := k.FinalizedBallotIDs.Set(ctx, id); err != nil {
		return err
	}

	ballot.Status = status
	if err := k.Ballots.Set(ctx, id, ballot); err != nil {
		return err
	}

	k.fireBallotTerminalHook(ctx, ballot.Id, ballot.BallotType, status)
	return nil
}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L60-75)
```go
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}

	// Step 3: Vote on inbound ballot (uses the original inbound data as-is for the ballot key,
	// so UVs that observe different field data will correctly produce different votes)
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
	if err != nil {
		return errors.Wrap(err, "failed to vote on inbound ballot")
	}

	commit()
```
