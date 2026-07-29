### Title
Free, unbounded on-chain account creation via `AccountInitDecorator` on failed gasless votes - (File: `app/ante/account_init_decorator.go`)

### Summary
Push Chain's gasless transaction pipeline lets any unprivileged attacker permanently grow chain state (the `AccountKeeper`/IAVL store) for zero cost by submitting spam transactions using gasless-whitelisted message types (e.g. `MsgVoteInbound`) signed by freshly generated, never-before-seen keys. The `AccountInitDecorator` unconditionally creates and commits a new on-chain `BaseAccount` for any new signer of a gasless message *before* the message content is validated by the actual message handler, and the ante-handler's state changes are committed independently of whether the subsequent message execution later fails.

### Finding Description
`app/txpolicy/gasless.go` defines a whitelist of message *types* (not signer identities) that qualify for gasless treatment: `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgExecutePayload`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`. [1](#0-0) 

`app/ante/account_init_decorator.go`'s `AccountInitDecorator.AnteHandle` runs early in the ante chain (`app/ante/ante_cosmos.go`, before `SetPubKeyDecorator`/`SigVerificationDecorator`). For any gasless tx whose single signer has no account yet, it verifies the signature against `account_number=0, sequence=0`, then unconditionally creates the account via `NewAccountWithAddress`/`SetAccount`, and returns `(ctx, nil)` **without calling `next`**, short-circuiting the remainder of the ante chain: [2](#0-1) 

Crucially, `MsgVoteInbound.ValidateBasic()` only requires a syntactically valid bech32 signer and a syntactically valid `Inbound` struct — it does not require the signer to be a registered/bonded Universal Validator: [3](#0-2) 

The actual authorization check — `IsBondedUniversalValidator` — only happens later, inside the `msgServer.VoteInbound` handler: [4](#0-3) 

In the standard Cosmos SDK `BaseApp.runTx` flow, the AnteHandler executes in its own cache-context that is **committed unconditionally as soon as the AnteHandler returns without an error**, while message execution (`runMsgs`) happens in a *separate* cache-context that is only committed if the message handler succeeds. Because `AccountInitDecorator` returns `(ctx, nil)` after creating the account, that account-creation state change is committed to the chain even though the subsequent `VoteInbound` message handler will reject the tx with "universal validator ... is not bonded" and roll back its own (separate) state branch. The account creation is not part of that rolled-back branch — it already landed in the parent context before message execution started.

Net effect: an attacker can generate an unlimited number of fresh Cosmos keypairs, craft a minimally-valid `MsgVoteInbound` (or any other gasless-whitelisted message type) for each, and broadcast them. Every such transaction:
- Pays no gas/fee (`DeductFeeDecorator` and `MinGasPriceDecorator` skip fee/min-gas checks for gasless txs),
- Fails at the message-handler stage because the signer is not a bonded UV,
- But **permanently creates a new `BaseAccount` in state** before failing.

This is a repeatable, cost-free way to grow on-chain account state without limit, defeating the implicit intent that the "gasless" carve-out exists only to bootstrap legitimate first-time UV hot keys or UEA-payload senders.

### Impact Explanation
This is a state-bloat / resource-exhaustion issue reachable by any unprivileged external actor without needing any privileged role, key compromise, or malicious validator/relayer assumption. It falls under the in-scope "denial of service ... not network-level and reachable without privileged control" category: the attack doesn't merely spam network bandwidth, it persists unbounded new state (accounts) in the canonical chain state at zero cost to the attacker, degrading state-sync time, snapshot/IAVL size, and export/import performance over time. It does not directly steal funds, but represents a genuine, root-caused breach of an intended resource-consumption invariant (gasless privileges should only be usable by parties that pass downstream authorization, not by anyone who can produce a syntactically valid message).

### Likelihood Explanation
High likelihood of triggerability: no special conditions are required beyond generating a keypair and submitting a transaction, which costs nothing under the gasless carve-out. The `Inbound.ValidateBasic()` bar (seen for `MsgVoteInbound`) is easily satisfiable with attacker-chosen strings. This can be scripted trivially and repeated indefinitely.

### Recommendation
- Have `AccountInitDecorator` verify eligibility (e.g., bonded-UV check for vote messages, or a lightweight authorization pre-check matching the eventual message-handler's authorization) *before* committing the new account, or
- Do not persist the newly created account outside of the same state branch used for message execution — i.e., defer account creation until after the message-level authorization succeeds, or
- Add a minimal proof-of-authorization/rate-limit gate (e.g., require the address to already be present in `UniversalValidatorSet`, or require a bonded stake) before allowing free account creation via the gasless path, so failed authorization does not leave residual state changes.

### Proof of Concept
1. Generate a new, never-used Cosmos keypair `K`.
2. Craft `MsgVoteInbound{ Signer: bech32(K), Inbound: { SourceChain: "eip155:1", TxHash: "0xdead...", Recipient: "0x0", Amount: "0", AssetAddr: "0x0", LogIndex: "0" } }` — passes `ValidateBasic()` trivially.
3. Sign the tx directly with `K` (not wrapped in `authz.MsgExec`) using `account_number=0, sequence=0`, with `gas=0`/no fee (message type is in the gasless allowlist, so `MinGasPriceDecorator`/`DeductFeeDecorator` do not require fee/balance).
4. Broadcast. The `AccountInitDecorator` verifies the signature against acct#0/seq#0, succeeds, creates and commits the new `BaseAccount` for `K`, and short-circuits (`return ctx, nil`).
5. Message execution proceeds to `msgServer.VoteInbound`, which calls `IsBondedUniversalValidator` on `K` and fails with `"universal validator ... is not bonded"`, reverting the message-level state changes.
6. Query `K`'s account on-chain (`pchaind query account <K>`) — the account exists and has `sequence=1`, despite the transaction's business logic having failed and cost the attacker nothing.
7. Repeat steps 1–6 indefinitely with new keys to grow state at zero attacker cost.

Note: step 5's exact runtime behavior depends on Cosmos SDK `BaseApp.runTx`'s ante/msg cache-context separation, which is vendored SDK logic outside this repository and could not be directly re-verified in the indexed source of this repo; this is standard, well-documented Cosmos SDK behavior (ante-handler branch commits independently of message-execution branch) but should be confirmed against the exact `cosmos-sdk`/`cosmos-evm` fork version pinned by this repository before treating this as fully confirmed.

### Citations

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

**File:** app/ante/account_init_decorator.go (L52-75)
```go
	newAccAddr := signers[0]
	if !aid.ak.HasAccount(ctx, newAccAddr) {
		ctx.Logger().Debug("account init decorator: new account detected on gasless tx, verifying signature",
			"address", sdk.AccAddress(newAccAddr).String(),
			"simulate", simulate,
		)
		// if account does not exist on chain, bypass rest of ante chain (especially gas and signature verification) here.
		// Perform signature verification on account number e and sequence number e instead.
		if err := aid.verifySignatureForNewAccount(ctx, tx, simulate); err != nil {
			ctx.Logger().Debug("account init decorator: signature verification failed for new account",
				"address", sdk.AccAddress(newAccAddr).String(),
				"error", err,
			)
			return ctx, err
		}

		acc := aid.ak.NewAccountWithAddress(ctx, newAccAddr)
		acc.SetSequence(1)
		aid.ak.SetAccount(ctx, acc)
		ctx.Logger().Info("account init decorator: new account created via gasless tx",
			"address", sdk.AccAddress(newAccAddr).String(),
		)
		return ctx, nil
	}
```

**File:** x/uexecutor/types/msg_vote_inbound.go (L52-59)
```go
// ValidateBasic does a sanity check on the provided data.
func (msg *MsgVoteInbound) ValidateBasic() error {
	// validate signer
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(err, "invalid signer address")
	}

	return msg.Inbound.ValidateBasic()
```

**File:** x/uexecutor/keeper/msg_server.go (L72-97)
```go
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
```
