### Title
Unbounded `ActiveBallotIDs` iteration on every ballot creation enables attacker-triggered DoS of crosschain vote finalization - (File: x/uvalidator/keeper/ballot.go)

### Summary
`CreateBallot` calls `ExpireBallotsBeforeHeight`, which fully iterates the entire `ActiveBallotIDs` key-set, on **every single new ballot creation** — i.e. on the first vote for every distinct inbound, outbound, chain-meta, TSS-key, or fund-migration observation. This is the same unbounded-loop pattern as GG-1's `distributeDividends`: a list that grows from ordinary, unprivileged user activity is walked in full inside a hot, per-transaction code path.

### Finding Description [1](#0-0) 
`CreateBallot` unconditionally invokes `k.ExpireBallotsBeforeHeight(ctx, blockHeight)` before creating the new ballot. `ExpireBallotsBeforeHeight` walks the full `ActiveBallotIDs` collection with `iter.Iterate(ctx, nil)`/`iter.Valid()`/`iter.Next()`, fetching every active `Ballot` to check its expiry height: [2](#0-1) 

`GetOrCreateBallot` (and therefore `CreateBallot`) is invoked from `VoteOnBallot`, which is the single generic entry point used by `x/uexecutor` (`MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`) and `x/utss` (`MsgVoteTssKeyProcess`, `MsgVoteFundMigration`) — all of which are gasless message types: [3](#0-2) [4](#0-3) 

Each `MsgVoteInbound` from an honest Universal Validator (UV) corresponds to a source-chain event the attacker itself created — a distinct `(source_chain, tx_hash, log_index)` inbound observation generates its own ballot ID via `GetInboundBallotKey`/`GetInboundUniversalTxKey`: [5](#0-4) 
An unprivileged attacker can cheaply generate a large number of distinct, tiny crosschain source-chain transactions (a bridging analog of "depositing miniscule LP to pool 0"). Honest UVs (assumed honest per scope) will faithfully vote on each of these, and each first vote on a distinct observation calls `CreateBallot`, which walks the *entire* current `ActiveBallotIDs` set — a set whose size is a direct function of how many distinct observations are currently in flight across ALL ballot types (inbound, outbound, chain-meta, TSS, migration), not bounded by any cap or batching.

Since ballot expiry is `DefaultExpiryAfterBlocks` blocks in the future (a fixed window; exact constant defined in `x/uexecutor/types/constants.go`, not fully inspected in this pass), an attacker who submits many distinct observations faster than that expiry window elapses causes `ActiveBallotIDs` to grow roughly linearly with attacker-submitted event count, while the per-new-ballot cost (`ExpireBallotsBeforeHeight`) grows linearly with the *current* set size — yielding roughly quadratic total keeper work for `N` attacker-generated observations submitted within one expiry window. All ballot types share the same `ActiveBallotIDs` collection, so this also degrades unrelated, legitimate flows (TSS key votes, fund-migration votes, chain-meta gas price votes) sharing the same underlying keyset.

### Impact Explanation
This falls under the allowed "denial of service... not network-level and reachable without privileged control" impact. Because the same `ActiveBallotIDs` walk gates ballot creation for inbound execution, outbound observation, chain-meta updates, and TSS/migration voting, sustained growth of this set can materially slow or, in the worst case (given enough attacker-submitted distinct low-cost source-chain events within the expiry window), make ordinary block processing of vote transactions prohibitively expensive, delaying or starving legitimate crosschain finalization (inbound execution, outbound TSS signing, chain-meta gas price updates) network-wide. This is a state-machine-level DoS surface reachable purely by an unprivileged external actor generating cheap source-chain transactions, without any peer/validator/relayer compromise.

### Likelihood Explanation
Likelihood is credible but not certain of practical severity: generating many distinct source-chain events cheaply (e.g. tiny-value transfers on a supported EVM/SVM chain) is inexpensive for an attacker, and each such event unavoidably becomes a new, distinct ballot ID once observed by honest UVs. The severity depends on (a) the actual value of `DefaultExpiryAfterBlocks` (not fully confirmed in this pass) relative to the rate at which new observations can be generated/expired, and (b) how many ballots typically stay active in steady state — both of which would need empirical/gas-profiling confirmation before treating this as high severity. It should be validated with a load test measuring keeper CPU/gas cost of `ExpireBallotsBeforeHeight` as `ActiveBallotIDs` size scales into the thousands.

### Recommendation
- Decouple expiry sweeping from the hot ballot-creation path: run `ExpireBallotsBeforeHeight` (or an equivalent) in `BeginBlock`/`EndBlock` with a strict per-block processing cap (batch a bounded number of expirable entries per block), rather than on every `CreateBallot` call.
- Alternatively, avoid full-set scans entirely by indexing active ballots by expiry height (e.g. a secondary `Map[expiryHeight]Set[ballotID]` or ordered collection) so expiry lookups are O(expired) rather than O(active).
- Consider a per-source-chain or per-UV rate limit / minimum bond-stake-weighted cost for generating new distinct ballot IDs to reduce the attacker's ability to cheaply grow `ActiveBallotIDs`.

### Proof of Concept
Conceptual PoC (not executed — would require an integration-test harness):
1. Set up a chain with enabled inbound chain config and a small `DefaultExpiryAfterBlocks`/vote window such that ballots remain active for many blocks.
2. As an attacker on the source chain, submit a large number (e.g. 5,000+) of minimal-value bridge transactions in rapid succession, each with a unique `tx_hash`/`log_index`.
3. Have honest UVs (test validators) call `ExecVoteInbound` for each distinct inbound, mirroring `x/uexecutor/keeper/msg_vote_inbound.go` flow, which drives `k.uvalidatorKeeper.VoteOnBallot` → `GetOrCreateBallot` → `CreateBallot` → `ExpireBallotsBeforeHeight`.
4. Measure the wall-clock/gas cost of the N-th `MsgVoteInbound` transaction as `ActiveBallotIDs` grows, and observe that voting-message processing time/gas grows with the number of concurrently active ballots (see `x/uvalidator/keeper/ballot.go:320-347` and `x/uvalidator/keeper/ballot.go:12-39`), confirming the O(active-ballots) cost per new observation as validated by existing integration tests such as `test/integration/uvalidator/ballot_voting_test.go` and `x/uvalidator/keeper/ballot_test.go` (which exercise `ExpireBallotsBeforeHeight` but do not test it at scale).

### Citations

**File:** x/uvalidator/keeper/ballot.go (L12-39)
```go
// CreateBallot creates a new ballot with the given parameters, stores it, and marks it as active.
func (k Keeper) CreateBallot(
	ctx context.Context,
	id string,
	ballotType types.BallotObservationType,
	eligibleVoters []string,
	votingThreshold int64,
	expiryAfterBlocks int64,
) (types.Ballot, error) {
	// Get current block height
	blockHeight, err := k.GetBlockHeight(ctx)
	if err != nil {
		return types.Ballot{}, err
	}

	k.Logger().Debug("creating ballot",
		"ballot_id", id,
		"ballot_type", ballotType.String(),
		"eligible_voters", len(eligibleVoters),
		"voting_threshold", votingThreshold,
		"expiry_after_blocks", expiryAfterBlocks,
		"block_height", blockHeight,
	)

	// First, expire any old ballots before this height
	if err := k.ExpireBallotsBeforeHeight(ctx, blockHeight); err != nil {
		return types.Ballot{}, err
	}
```

**File:** x/uvalidator/keeper/ballot.go (L320-347)
```go
// ExpireBallotsBeforeHeight checks active ballots and marks expired ones.
// It uses a two-phase approach: first collect IDs to expire, then mutate,
// to avoid modifying the ActiveBallotIDs collection during iteration.
func (k Keeper) ExpireBallotsBeforeHeight(ctx context.Context, currentHeight int64) error {
	iter, err := k.ActiveBallotIDs.Iterate(ctx, nil)
	if err != nil {
		return err
	}

	// Phase 1: collect IDs to expire
	var toExpire []string
	for ; iter.Valid(); iter.Next() {
		id, err := iter.Key()
		if err != nil {
			iter.Close()
			return err
		}

		ballot, err := k.Ballots.Get(ctx, id)
		if err != nil {
			iter.Close()
			return err
		}

		if ballot.BlockHeightExpiry <= currentHeight {
			toExpire = append(toExpire, id)
		}
	}
```

**File:** x/uvalidator/keeper/voting.go (L120-146)
```go
func (k Keeper) VoteOnBallot(
	ctx context.Context,
	id string,
	ballotType types.BallotObservationType,
	voter string,
	voteResult types.VoteResult,
	voters []string,
	votesNeeded int64,
	expiryAfterBlocks int64,
) (
	ballot types.Ballot,
	isFinalized bool,
	isNew bool,
	err error) {

	k.Logger().Debug("vote on ballot",
		"ballot_id", id,
		"ballot_type", ballotType.String(),
		"voter", voter,
		"vote_result", voteResult.String(),
		"votes_needed", votesNeeded,
	)

	ballot, isNew, err = k.GetOrCreateBallot(ctx, id, ballotType, voters, votesNeeded, expiryAfterBlocks)
	if err != nil {
		return ballot, false, false, errors.Wrap(err, "Error while voting on the ballot")
	}
```

**File:** app/txpolicy/gasless.go (L14-26)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L43-66)
```go
	// Step 1: Derive UTX key from the original inbound data (source_chain:tx_hash:log_index)
	universalTxKey := types.GetInboundUniversalTxKey(inbound)
	found, err := k.HasUniversalTx(ctx, universalTxKey)
	if err != nil {
		return errors.Wrap(err, "failed to check UniversalTx")
	}
	if found {
		k.Logger().Warn("vote inbound rejected: utx already exists", "utx_key", universalTxKey)
		return fmt.Errorf("universal tx with key %s already exists", universalTxKey)
	}

	// use a temporary context to not commit any ballot state change in case of error
	tmpCtx, commit := sdkCtx.CacheContext()

	// Step 2: Record this validator's vote in the per-utx PendingInbounds entry
	// (variant-aware audit trail). Each unique Inbound payload becomes its own
	// variant; multiple variants per utx_key indicate validator divergence.
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}
```
