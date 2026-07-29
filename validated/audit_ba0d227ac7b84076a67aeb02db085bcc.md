[1](#0-0) [2](#0-1)

### Citations

**File:** x/utss/keeper/msg_server.go (L114-138)
```go
// VoteFundMigration implements types.MsgServer.
func (ms msgServer) VoteFundMigration(ctx context.Context, msg *types.MsgVoteFundMigration) (*types.MsgVoteFundMigrationResponse, error) {
	signerAccAddr, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, fmt.Errorf("invalid signer address: %w", err)
	}

	signerValAddr := sdk.ValAddress(signerAccAddr)

	isBonded, err := ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check bonded status for signer %s", msg.Signer)
	}
	if !isBonded {
		return nil, fmt.Errorf("universal validator for signer %s is not bonded", msg.Signer)
	}

	isTombstoned, err := ms.k.uvalidatorKeeper.IsTombstonedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check tombstoned status for signer %s", msg.Signer)
	}
	if isTombstoned {
		return nil, fmt.Errorf("universal validator for signer %s is tombstoned", msg.Signer)
	}

```

**File:** x/utss/keeper/voting.go (L103-151)
```go
func (k Keeper) VoteOnFundMigrationBallot(
	ctx context.Context,
	universalValidator sdk.ValAddress,
	migrationId uint64,
	txHash string,
	success bool,
) (isFinalized bool, isNew bool, err error) {

	ballotKey := types.GetFundMigrationBallotKey(migrationId, txHash, success)

	universalValidatorSet, err := k.uvalidatorKeeper.GetEligibleVoters(ctx)
	if err != nil {
		return false, false, err
	}

	totalValidators := len(universalValidatorSet)
	votesNeeded := (fundMigrationVotesNumerator*totalValidators)/fundMigrationVotesDenominator + 1

	validatorStrs := make([]string, len(universalValidatorSet))
	for i, v := range universalValidatorSet {
		validatorStrs[i] = v.IdentifyInfo.CoreValidatorAddress
	}

	voteResult := uvalidatortypes.VoteResult_VOTE_RESULT_SUCCESS
	if !success {
		voteResult = uvalidatortypes.VoteResult_VOTE_RESULT_FAILURE
	}

	k.Logger().Debug("voting on fund migration ballot",
		"ballot_key", ballotKey,
		"validator", universalValidator.String(),
		"migration_id", migrationId,
		"total_validators", totalValidators,
		"votes_needed", votesNeeded,
	)

	_, isFinalized, isNew, err = k.uvalidatorKeeper.VoteOnBallot(
		ctx,
		ballotKey,
		uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_FUND_MIGRATION,
		universalValidator.String(),
		voteResult,
		validatorStrs,
		int64(votesNeeded),
		int64(fundMigrationExpiryBlocks),
	)
	if err != nil {
		return false, false, err
	}
```
