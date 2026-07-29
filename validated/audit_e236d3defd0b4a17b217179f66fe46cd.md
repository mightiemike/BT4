## Title
Gasless message whitelist grants free execution/signature-verification cycles to unauthenticated senders, enabling unmetered flooding analogous to SEDA H-12 - (File: `app/txpolicy/gasless.go`)

### Summary
Push Chain's ante pipeline grants free (fee-exempt, min-gas-exempt) transaction processing to any transaction whose message types match a static whitelist in `IsGaslessTx`, with **no check that the signer is actually authorized to send that message type** (e.g., a bonded Universal Validator). The eligibility check that does exist (`IsBondedUniversalValidator`) only runs deep inside the message server, *after* the entire ante chain — including cryptographic signature verification and on-the-fly account creation — has already executed for free. This is the same root-cause class as SEDA H-12: a message category is marked "free gas eligible" based on a cheap, coarse check (there: message type + a contract query; here: message type alone), while the real authorization/validity check happens later and doesn't gate fee-exemption. An attacker can therefore flood the network with syntactically valid but semantically unauthorized gasless transactions at zero cost.

### Finding Description
The gasless whitelist is defined purely by message `TypeUrl`, with no signer/authorization check: [1](#0-0) 

This whitelist includes `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta` (bonded-UV-only messages) and `MsgExecutePayload`/`MsgMigrateUEA` (open to any signer). `IsGaslessTx` is consumed by three ante decorators that strip away every cost signal before the message ever reaches its handler:

- `MinGasPriceDecorator` skips the FeeMarket minimum-fee check for gasless txs: [2](#0-1) 
- `DeductFeeDecorator` skips fee deduction entirely for gasless txs: [3](#0-2) 
- `AccountInitDecorator` will silently create a brand-new on-chain account (sequence=1, no funding required) for any never-before-seen signer of a gasless tx, verifying only a self-consistent signature over `account_number=0, sequence=0` — i.e., an attacker-controlled freshly generated keypair: [4](#0-3) 

Crucially, the actual authorization check for the "bonded-UV-only" messages (`IsBondedUniversalValidator` / `IsTombstonedUniversalValidator`) lives entirely inside the `msgServer`, not the ante chain: [5](#0-4) 

Since Cosmos SDK's `CheckTx` only runs the `AnteHandler` (not message handlers), a transaction containing e.g. `MsgVoteInbound` signed by an attacker-controlled, non-UV key will:
1. Pass `CheckTx` (ante only checks message *type*, not signer eligibility) and enter every node's mempool for free.
2. Get gossiped across the p2p network.
3. Consume real CPU on every validator for `SigVerificationDecorator`'s cryptographic signature check and for `AccountInitDecorator`'s new-account creation/state write — all before the tx is even included in a block.
4. Only fail once actually included in a block and routed to `msgServer.VoteInbound`, where `IsBondedUniversalValidator` finally rejects it — but by then the CPU, bandwidth, mempool slot, and state-write costs have already been paid by the network, not the attacker.

Because `AccountInitDecorator` provisions a fresh, zero-balance account for any never-seen signer at no cost, an attacker can generate unlimited throwaway keypairs (free, off-chain) and repeat this indefinitely, each iteration consuming full signature-verification and account-creation costs on every validator while costing the attacker nothing on-chain. This is a broader version of the SEDA bug: SEDA's flaw let attackers replay *the same eligible message type* for free; here, the "free-gas eligible" gate doesn't even check *sender eligibility* at all, only the message's `TypeUrl`.

### Impact Explanation
This falls within the allowed "denial of service ... not network-level and reachable without privileged control" impact. An unprivileged attacker can:
- Force unbounded on-chain account creation (state bloat) via `AccountInitDecorator`, one entry per throwaway keypair, entirely gasless.
- Force unbounded cryptographic signature verification and mempool/gossip overhead on every honest validator by submitting `MsgVoteInbound`/`MsgVoteOutbound`/`MsgVoteChainMeta`/`MsgVoteTssKeyProcess`/`MsgVoteFundMigration` transactions from non-UV accounts that will pass `CheckTx` and only fail deep in `DeliverTx`'s message handler.
- Because the cost of production (signature computation, tx construction) is trivial and asymmetric to the cost of validation (ECDSA verify, KV write, ante chain traversal) borne by every full node, this can materially delay block processing / degrade node responsiveness — the same class of harm identified in SEDA H-12.

### Likelihood Explanation
High. No special privileges, staking, or prior on-chain state are required. An attacker only needs to generate keypairs (free) and craft protobuf messages with the right `TypeUrl` and any well-formed (self-consistent, since account_number/sequence start at 0) signature. This is fully reachable through the ordinary public transaction-submission path with honest validators and honest nodes.

### Recommendation
Move signer-eligibility validation (e.g., `IsBondedUniversalValidator`/`IsTombstonedUniversalValidator` for vote messages) into the ante chain — specifically into `IsGaslessTx` or a dedicated ante decorator that runs *before* `AccountInitDecorator` and `DeductFeeDecorator` — so that fee-exemption and free account creation are only granted to transactions from senders who are actually authorized to submit that gasless message type. Alternatively, charge minimal gas upfront for all gasless-candidate messages and refund it only after successful, authorized execution, mirroring the SEDA fix's "charge gas up front and refund" mitigation.

### Proof of Concept
1. Generate an arbitrary new Cosmos keypair `K` (free, off-chain), deriving `signerAddr`.
2. Construct a `MsgVoteInbound{Signer: signerAddr, Inbound: <arbitrary well-formed Inbound>}` and wrap it in a transaction signed by `K` with `account_number=0, sequence=0` (matching `AccountInitDecorator`'s expectations for a never-before-seen account).
3. Submit the transaction. `IsGaslessTx` returns true (message type matches whitelist) → `MinGasPriceDecorator`/`DeductFeeDecorator` skip fee checks → `AccountInitDecorator` verifies the self-consistent signature and creates the account on the spot, at zero balance and zero fee.
4. Transaction passes `CheckTx`, propagates through the mempool, and is included in a block; `msgServer.VoteInbound` then rejects it via `IsBondedUniversalValidator` (`signerAddr` is not a UV), but signature verification, account creation, and block/mempool bandwidth have already been consumed for free.
5. Repeat with a new keypair for each transaction to bypass sequence-based rate limiting, at unbounded scale and zero cost to the attacker.

### Citations

**File:** app/txpolicy/gasless.go (L12-49)
```go
// IsGaslessTx checks if a transaction contains only allowed gasless message types
// Returns true if all messages in the transaction are in the allowed gasless message types
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

	msgs := tx.GetMsgs()
	if len(msgs) == 0 {
		return false
	}

	for _, msg := range msgs {
		switch m := msg.(type) {
		case *authz.MsgExec:
			// Only gasless if ALL inner messages are allowed
			for _, innerMsg := range m.Msgs {
				if !slices.Contains(GaslessMsgTypes, innerMsg.TypeUrl) {
					return false
				}
			}
		default:
			if !slices.Contains(GaslessMsgTypes, sdk.MsgTypeURL(msg)) {
				return false
			}
		}
	}
	return true
}
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```

**File:** app/ante/account_init_decorator.go (L31-81)
```go
func (aid AccountInitDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	if !txpolicy.IsGaslessTx(tx) {
		// Skip account initialization for non-gasless transactions
		ctx.Logger().Debug("account init decorator: non-gasless tx, skipping account init")
		return next(ctx, tx, simulate)
	}

	sigTx, ok := tx.(authsigning.Tx)
	if !ok {
		return ctx, errorsmod.Wrap(sdkerrors.ErrTxDecode, "invalid transaction type")
	}

	signers, err := sigTx.GetSigners()
	if err != nil || len(signers) != 1 {
		ctx.Logger().Debug("account init decorator: could not get unique signer, passing to next handler",
			"num_signers", len(signers),
			"error", err,
		)
		return next(ctx, tx, simulate)
	}

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

	ctx.Logger().Debug("account init decorator: existing account on gasless tx, passing to next handler",
		"address", sdk.AccAddress(newAccAddr).String(),
	)
	return next(ctx, tx, simulate)
}
```

**File:** x/uexecutor/keeper/msg_server.go (L72-106)
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

	// continue with inbound synthetic creation / voting logic here
	err = ms.k.VoteInbound(ctx, signerValAddr, *msg.Inbound)
	if err != nil {
		return nil, err
	}

	return &types.MsgVoteInboundResponse{}, nil
}
```
