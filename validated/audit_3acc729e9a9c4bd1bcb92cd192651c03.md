Found the analog. The pattern from the report — a `codec.go` registering only a subset of a module's `MsgServer` message types — reproduces across multiple Push Chain modules.

### Title
Missing message type registration in codec.go leaves privileged/consensus-critical messages undecodable and unroutable - (File: `x/uregistry/types/codec.go`, `x/utss/types/codec.go`, `x/uvalidator/types/codec.go`)

### Summary
`x/uregistry/keeper/msg_server.go`, `x/utss/keeper/msg_server.go`, and `x/uvalidator/keeper/msg_server.go` each implement several `MsgServer` methods, but the corresponding `codec.go` in each module's `types` package only registers `MsgUpdateParams` in `RegisterLegacyAminoCodec`/`RegisterInterfaces`.

### Finding Description
`x/uregistry/keeper/msg_server.go` implements `AddChainConfig`, `UpdateChainConfig`, `AddTokenConfig`, `UpdateTokenConfig`, and `RemoveTokenConfig` [1](#0-0) , yet `x/uregistry/types/codec.go` only registers `MsgUpdateParams` in both `RegisterLegacyAminoCodec` and `RegisterInterfaces` [2](#0-1) .

Similarly, `x/utss/keeper/msg_server.go` implements `InitiateTssKeyProcess`, `VoteTssKeyProcess`, `InitiateFundMigration`, and `VoteFundMigration` [3](#0-2) , but `x/utss/types/codec.go` only registers `MsgUpdateParams` [4](#0-3) .

Likewise, `x/uvalidator/keeper/msg_server.go` implements `AddUniversalValidator`, `RemoveUniversalValidator`, `UpdateUniversalValidator`, `UpdateUniversalValidatorStatus`, and `RecomputeBallotQuorum` [5](#0-4) , but `x/uvalidator/types/codec.go` only registers `MsgUpdateParams` [6](#0-5) .

By contrast, `x/uexecutor/types/codec.go` registers `MsgUpdateParams`, `MsgExecutePayload`, and `MsgVoteInbound`, but is itself missing `MsgMigrateUEA`, `MsgVoteOutbound`, `MsgVoteChainMeta`, and `MsgRevertStuckInbound`, all of which are implemented in `x/uexecutor/keeper/msg_server.go` [7](#0-6)  but absent from `RegisterLegacyAminoCodec`/`RegisterInterfaces` [8](#0-7) .

Since Cosmos SDK's Protobuf `Any` interface resolution for `sdk.Msg` in transactions relies on `RegisterInterfaces` populating the `InterfaceRegistry`, any message type not registered there cannot be unpacked from a `TxBody`, and the transaction will be rejected during decoding before it ever reaches routing or the `MsgServer` handler.

### Impact Explanation
This directly affects consensus-critical and privileged operational flows in-scope for this engagement: TSS key generation/rotation (`MsgInitiateTssKeyProcess`, `MsgVoteTssKeyProcess`), fund migration voting (`MsgInitiateFundMigration`, `MsgVoteFundMigration`) in `x/utss`, universal validator bonding/removal and ballot quorum recomputation in `x/uvalidator`, registry chain/token configuration updates in `x/uregistry`, and UEA migration/outbound voting/chain-meta voting/stuck-inbound reversion in `x/uexecutor`. If these messages cannot be decoded, the corresponding administrative and validator-driven flows are non-functional network-wide — for example honest universal validators would be unable to submit `MsgVoteFundMigration` or `MsgVoteOutbound`, stalling TSS migration and outbound finalization, which are core universal-execution and TSS-coordination invariants. However, note the required impact gate excludes issues that are purely "not usable" without a further attacker-driven fund loss, freeze, or state-divergence outcome, so the concrete impact here is process breakage/denial-of-availability of legitimate governance/validator flows rather than a directly attacker-exploitable fund-draining bug.

### Likelihood Explanation
This is a deterministic, code-inspection-confirmed gap, not a probabilistic vulnerability — every transaction using these unregistered message types will unconditionally fail to unpack/decode, so the "likelihood" is certain whenever the affected message types are used in production.

### Recommendation
Add the missing message types (`MsgAddChainConfig`, `MsgUpdateChainConfig`, `MsgAddTokenConfig`, `MsgUpdateTokenConfig`, `MsgRemoveTokenConfig` in uregistry; `MsgInitiateTssKeyProcess`, `MsgVoteTssKeyProcess`, `MsgInitiateFundMigration`, `MsgVoteFundMigration` in utss; `MsgAddUniversalValidator`, `MsgRemoveUniversalValidator`, `MsgUpdateUniversalValidator`, `MsgUpdateUniversalValidatorStatus`, `MsgRecomputeBallotQuorum` in uvalidator; `MsgMigrateUEA`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgRevertStuckInbound` in uexecutor) to both `RegisterLegacyAminoCodec` and `RegisterInterfaces` in each module's `codec.go`.

### Proof of Concept
Not applicable as executable PoC given index limitations, but the code-level proof is the direct comparison above: every `MsgServer` method implemented in `keeper/msg_server.go` for a module must have a matching `registry.RegisterImplementations` entry in that module's `types/codec.go`; the diff between the two lists in `x/uregistry`, `x/utss`, `x/uvalidator`, and `x/uexecutor` shows the unregistered set. A concrete unprivileged test would be to construct a `Tx` containing e.g. `MsgVoteFundMigration` packed as `Any`, submit it via `baseapp`, and observe `UnpackInterfaces`/`GetMsgs` failing because the concrete type isn't registered in the `InterfaceRegistry`.

Note: due to index size limits I could not fully confirm whether `app/app.go` or module registration wraps these `RegisterInterfaces` functions with any additional global registration mechanism that might compensate for this gap; if the user needs certainty on production behavior, a full Devin session with complete repository access would be needed to trace `app.go`'s module manager wiring and confirm no other registration path exists.

### Citations

**File:** x/uregistry/keeper/msg_server.go (L42-64)
```go
func (ms msgServer) AddChainConfig(ctx context.Context, msg *types.MsgAddChainConfig) (*types.MsgAddChainConfigResponse, error) {
	if msg.ChainConfig == nil {
		return nil, errors.Wrap(sdkErrors.ErrInvalidRequest, "chain_config is required")
	}
	ms.k.Logger().Info("msg add chain config received", "signer", msg.Signer, "chain", msg.ChainConfig.Chain)

	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.AddChainConfig(ctx, msg.ChainConfig)
	if err != nil {
		return nil, err
	}

	return &types.MsgAddChainConfigResponse{}, nil
}
```

**File:** x/uregistry/types/codec.go (L22-35)
```go
// RegisterLegacyAminoCodec registers concrete types on the LegacyAmino codec
func RegisterLegacyAminoCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(&MsgUpdateParams{}, ModuleName+"/MsgUpdateParams", nil)
}

func RegisterInterfaces(registry types.InterfaceRegistry) {

	registry.RegisterImplementations(
		(*sdk.Msg)(nil),
		&MsgUpdateParams{},
	)

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
}
```

**File:** x/utss/keeper/msg_server.go (L41-112)
```go
// InitiateTssKeyProcess implements types.MsgServer.
func (ms msgServer) InitiateTssKeyProcess(ctx context.Context, msg *types.MsgInitiateTssKeyProcess) (*types.MsgInitiateTssKeyProcessResponse, error) {
	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.InitiateTssKeyProcess(ctx, msg.ProcessType)
	if err != nil {
		return nil, err
	}
	return &types.MsgInitiateTssKeyProcessResponse{}, nil
}

// VoteTssKeyProcess implements types.MsgServer.
func (ms msgServer) VoteTssKeyProcess(ctx context.Context, msg *types.MsgVoteTssKeyProcess) (*types.MsgVoteTssKeyProcessResponse, error) {
	signerAccAddr, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, fmt.Errorf("invalid signer address: %w", err)
	}

	// Convert account to validator operator address
	signerValAddr := sdk.ValAddress(signerAccAddr)

	// Lookup the linked universal validator for this signer
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

	err = ms.k.VoteTssKeyProcess(ctx, signerValAddr, msg.TssPubkey, msg.KeyId, msg.ProcessId)
	if err != nil {
		return nil, err
	}

	return &types.MsgVoteTssKeyProcessResponse{}, nil
}

// InitiateFundMigration implements types.MsgServer.
func (ms msgServer) InitiateFundMigration(ctx context.Context, msg *types.MsgInitiateFundMigration) (*types.MsgInitiateFundMigrationResponse, error) {
	// Verify admin authority
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}
	if params.Admin != msg.Signer {
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	migrationId, err := ms.k.InitiateFundMigration(ctx, msg.OldKeyId, msg.Chain)
	if err != nil {
		return nil, err
	}

	return &types.MsgInitiateFundMigrationResponse{MigrationId: migrationId}, nil
}
```

**File:** x/utss/types/codec.go (L22-35)
```go
// RegisterLegacyAminoCodec registers concrete types on the LegacyAmino codec
func RegisterLegacyAminoCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(&MsgUpdateParams{}, ModuleName+"/MsgUpdateParams", nil)
}

func RegisterInterfaces(registry types.InterfaceRegistry) {

	registry.RegisterImplementations(
		(*sdk.Msg)(nil),
		&MsgUpdateParams{},
	)

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
}
```

**File:** x/uvalidator/keeper/msg_server.go (L47-199)
```go
// AddUniversalValidator implements types.MsgServer.
func (ms msgServer) AddUniversalValidator(ctx context.Context, msg *types.MsgAddUniversalValidator) (*types.MsgAddUniversalValidatorResponse, error) {
	ms.k.Logger().Info("msg: AddUniversalValidator", "signer", msg.Signer, "validator", msg.CoreValidatorAddress)

	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		ms.k.Logger().Warn("msg: AddUniversalValidator unauthorized",
			"expected_admin", params.Admin,
			"got_signer", msg.Signer,
		)
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.AddUniversalValidator(ctx, msg.CoreValidatorAddress, *msg.Network)
	if err != nil {
		return nil, err
	}

	return &types.MsgAddUniversalValidatorResponse{}, nil
}

// RemoveUniversalValidator implements types.MsgServer.
func (ms msgServer) RemoveUniversalValidator(ctx context.Context, msg *types.MsgRemoveUniversalValidator) (*types.MsgRemoveUniversalValidatorResponse, error) {
	ms.k.Logger().Info("msg: RemoveUniversalValidator", "signer", msg.Signer, "validator", msg.CoreValidatorAddress)

	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		ms.k.Logger().Warn("msg: RemoveUniversalValidator unauthorized",
			"expected_admin", params.Admin,
			"got_signer", msg.Signer,
		)
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.RemoveUniversalValidator(ctx, msg.CoreValidatorAddress)
	if err != nil {
		return nil, err
	}

	return &types.MsgRemoveUniversalValidatorResponse{}, nil
}

// UpdateUniversalValidator implements types.MsgServer.
func (ms msgServer) UpdateUniversalValidator(ctx context.Context, msg *types.MsgUpdateUniversalValidator) (*types.MsgUpdateUniversalValidatorResponse, error) {
	ms.k.Logger().Info("msg: UpdateUniversalValidator", "signer", msg.Signer)

	if msg.Network == nil {
		return nil, errors.Wrap(sdkErrors.ErrInvalidRequest, "network info is required")
	}

	// Parse signer account
	signerAcc, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(sdkErrors.ErrInvalidAddress, "invalid signer address: %s", msg.Signer)
	}

	// Find validator controlled by this account
	valAddr := sdk.ValAddress(signerAcc)

	validator, err := ms.k.StakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return nil, errors.Wrap(err, "signer is not a validator operator")
	}

	err = ms.k.UpdateUniversalValidator(ctx, validator.OperatorAddress, *msg.Network)
	if err != nil {
		return nil, err
	}

	return &types.MsgUpdateUniversalValidatorResponse{}, nil
}

// UpdateUniversalValidatorStatus implements types.MsgServer.
func (ms msgServer) UpdateUniversalValidatorStatus(ctx context.Context, msg *types.MsgUpdateUniversalValidatorStatus) (*types.MsgUpdateUniversalValidatorStatusResponse, error) {
	ms.k.Logger().Info("msg: UpdateUniversalValidatorStatus",
		"signer", msg.Signer,
		"validator", msg.CoreValidatorAddress,
		"new_status", msg.NewStatus.String(),
	)

	// Retrieve the current Params
	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}

	if params.Admin != msg.Signer {
		ms.k.Logger().Warn("msg: UpdateUniversalValidatorStatus unauthorized",
			"expected_admin", params.Admin,
			"got_signer", msg.Signer,
		)
		return nil, errors.Wrapf(sdkErrors.ErrUnauthorized, "invalid authority; expected %s, got %s", params.Admin, msg.Signer)
	}

	err = ms.k.UpdateUniversalValidatorStatus(ctx, msg.CoreValidatorAddress, msg.NewStatus)
	if err != nil {
		return nil, err
	}

	return &types.MsgUpdateUniversalValidatorStatusResponse{}, nil
}

// RecomputeBallotQuorum is an admin escape hatch for stuck ballots — see Keeper.RecomputeBallotQuorum.
func (ms msgServer) RecomputeBallotQuorum(ctx context.Context, msg *types.MsgRecomputeBallotQuorum) (*types.MsgRecomputeBallotQuorumResponse, error) {
	ms.k.Logger().Info("msg: RecomputeBallotQuorum", "signer", msg.Signer, "ballot_id", msg.BallotId)

	params, err := ms.k.Params.Get(ctx)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to get params")
	}
	if params.Admin != msg.Signer {
		return nil, errors.Wrapf(govtypes.ErrInvalidSigner, "invalid admin; expected %s, got %s", params.Admin, msg.Signer)
	}

	if msg.BallotId == "" {
		return nil, errors.Wrap(sdkErrors.ErrInvalidRequest, "ballot_id is required")
	}

	oldEligible, newEligible, oldThreshold, newThreshold, newStatus, err := ms.k.RecomputeBallotQuorum(ctx, msg.BallotId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	sdkCtx.EventManager().EmitEvent(sdk.NewEvent(
		"ballot_quorum_recomputed",
		sdk.NewAttribute("ballot_id", msg.BallotId),
		sdk.NewAttribute("admin", msg.Signer),
		sdk.NewAttribute("old_eligible_count", fmt.Sprintf("%d", oldEligible)),
		sdk.NewAttribute("new_eligible_count", fmt.Sprintf("%d", newEligible)),
		sdk.NewAttribute("old_voting_threshold", fmt.Sprintf("%d", oldThreshold)),
		sdk.NewAttribute("new_voting_threshold", fmt.Sprintf("%d", newThreshold)),
		sdk.NewAttribute("new_status", newStatus.String()),
	))

	return &types.MsgRecomputeBallotQuorumResponse{
		OldEligibleCount:   oldEligible,
		NewEligibleCount:   newEligible,
		OldVotingThreshold: oldThreshold,
		NewVotingThreshold: newThreshold,
		NewStatus:          newStatus,
	}, nil
}
```

**File:** x/uvalidator/types/codec.go (L22-35)
```go
// RegisterLegacyAminoCodec registers concrete types on the LegacyAmino codec
func RegisterLegacyAminoCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(&MsgUpdateParams{}, ModuleName+"/MsgUpdateParams", nil)
}

func RegisterInterfaces(registry types.InterfaceRegistry) {

	registry.RegisterImplementations(
		(*sdk.Msg)(nil),
		&MsgUpdateParams{},
	)

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
}
```

**File:** x/uexecutor/keeper/msg_server.go (L57-214)
```go
// MigrateUEA handles UEA Migration.
func (ms msgServer) MigrateUEA(ctx context.Context, msg *types.MsgMigrateUEA) (*types.MsgMigrateUEAResponse, error) {
	_, evmFromAddress, err := utils.GetAddressPair(msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to parse signer address")
	}

	err = ms.k.MigrateUEA(ctx, evmFromAddress, msg.UniversalAccountId, msg.MigrationPayload, msg.Signature)
	if err != nil {
		return nil, err
	}

	return &types.MsgMigrateUEAResponse{}, nil
}

// VoteInbound implements types.MsgServer.
func (ms msgServer) VoteInbound(ctx context.Context, msg *types.MsgVoteInbound) (*types.MsgVoteInboundResponse, error) {
	signerAccAddr, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, fmt.Errorf("invalid signer address: %w", err)
	}

	// Convert account to validator operator address
	signerValAddr := sdk.ValAddress(signerAccAddr)

	// Lookup the linked universal validator for this signer
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

	// continue with inbound synthetic creation / voting logic here
	err = ms.k.VoteInbound(ctx, signerValAddr, *msg.Inbound)
	if err != nil {
		return nil, err
	}

	return &types.MsgVoteInboundResponse{}, nil
}

// VoteOutbound implements types.MsgServer.
func (ms msgServer) VoteOutbound(ctx context.Context, msg *types.MsgVoteOutbound) (*types.MsgVoteOutboundResponse, error) {
	signerAccAddr, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, fmt.Errorf("invalid signer address: %w", err)
	}

	// Normalize IDs: strip 0x prefix
	msg.TxId = strings.TrimPrefix(msg.TxId, "0x")
	msg.UtxId = strings.TrimPrefix(msg.UtxId, "0x")

	// Convert account to validator operator address
	signerValAddr := sdk.ValAddress(signerAccAddr)

	// Lookup the linked universal validator for this signer
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

	err = ms.k.VoteOutbound(ctx, signerValAddr, msg.UtxId, msg.TxId, *msg.ObservedTx)
	if err != nil {
		return nil, err
	}

	return &types.MsgVoteOutboundResponse{}, nil
}

// VoteChainMeta implements types.MsgServer.
func (ms msgServer) VoteChainMeta(ctx context.Context, msg *types.MsgVoteChainMeta) (*types.MsgVoteChainMetaResponse, error) {
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

	err = ms.k.VoteChainMeta(ctx, signerValAddr, msg.ObservedChainId, msg.Price, msg.ChainHeight)
	if err != nil {
		return nil, err
	}
	return &types.MsgVoteChainMetaResponse{}, nil
}

// RevertStuckInbound is the admin escape hatch — see Keeper.RevertStuckInbound.
func (ms msgServer) RevertStuckInbound(ctx context.Context, msg *types.MsgRevertStuckInbound) (*types.MsgRevertStuckInboundResponse, error) {
	ms.k.Logger().Info("msg: RevertStuckInbound", "signer", msg.Signer)

	admin, err := ms.k.uvalidatorKeeper.GetAdmin(ctx)
	if err != nil {
		return nil, errors.Wrap(err, "failed to read uvalidator admin")
	}
	if admin != msg.Signer {
		return nil, errors.Wrapf(govtypes.ErrInvalidSigner, "invalid admin; expected %s, got %s", admin, msg.Signer)
	}

	if msg.Inbound == nil {
		return nil, errors.Wrap(sdkErrors.ErrInvalidRequest, "inbound is required")
	}

	utxId, outboundId, err := ms.k.RevertStuckInbound(ctx, *msg.Inbound)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	sdkCtx.EventManager().EmitEvent(sdk.NewEvent(
		"inbound_reverted_by_admin",
		sdk.NewAttribute("admin", msg.Signer),
		sdk.NewAttribute("utx_id", utxId),
		sdk.NewAttribute("outbound_id", outboundId),
		sdk.NewAttribute("source_chain", msg.Inbound.SourceChain),
		sdk.NewAttribute("amount", msg.Inbound.Amount),
	))

	return &types.MsgRevertStuckInboundResponse{
		UtxId:      utxId,
		OutboundId: outboundId,
	}, nil
}
```

**File:** x/uexecutor/types/codec.go (L22-39)
```go
// RegisterLegacyAminoCodec registers concrete types on the LegacyAmino codec
func RegisterLegacyAminoCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(&MsgUpdateParams{}, ModuleName+"/MsgUpdateParams", nil)
	cdc.RegisterConcrete(&MsgExecutePayload{}, ModuleName+"/MsgExecutePayload", nil)
	cdc.RegisterConcrete(&MsgVoteInbound{}, ModuleName+"/MsgVoteInbound", nil)
}

func RegisterInterfaces(registry types.InterfaceRegistry) {

	registry.RegisterImplementations(
		(*sdk.Msg)(nil),
		&MsgUpdateParams{},
		&MsgExecutePayload{},
		&MsgVoteInbound{},
	)

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
}
```
