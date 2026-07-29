## Analysis

The external report describes an unauthenticated, cost-free request causing a node to create and persist an unbounded, never-cleaned cache entry, exhausting memory. The scoped Push Chain analog is in the gasless AnteHandler pipeline, not in `universalClient/` caches (those already have TTL-based cleaners like `EventCleaner`, `expirysweeper.Sweeper`, and `Coordinator.validatorsSnapshot`'s staleness guard, all of which reject the "unbounded pruning bypass" pattern). The scoped custom `AccountInitDecorator` behaves differently: it unconditionally persists a brand-new on-chain account for *any* gasless tx signed by a never-before-seen address, before the underlying message is validated for authorization, and with zero fee, zero balance requirement, and no cleanup path. [1](#0-0) 

`IsGaslessTx` treats any tx whose messages are all in a small whitelist (including `MsgExecutePayload`, explicitly documented as submittable by "any account") as gasless, and the ante chain skips fee deduction and min-gas-price checks for these. [2](#0-1) [3](#0-2) 

The decorator ordering places `DeductFeeDecorator` (which no-ops for gasless txs) before `AccountInitDecorator`: [4](#0-3) 

### Title
Free, unbounded on-chain account creation via gasless AnteHandler `AccountInitDecorator` - (`File: app/ante/account_init_decorator.go`)

### Summary
An unprivileged attacker can generate unlimited fresh keypairs and, at zero cost (no fee, no funding, no bonded-validator status), get `AccountInitDecorator.AnteHandle` to permanently write a new `BaseAccount` entry into the `auth` module's KV-store for each one, regardless of whether the wrapped gasless message (e.g. `MsgVoteInbound`, `MsgVoteChainMeta`, `MsgExecutePayload`) ultimately succeeds or is even authorized. This mirrors the reported bug class: a cheap, attacker-controlled input triggers creation of a persistent server-side state entry with no pruning and no cost gate.

### Finding Description
`AccountInitDecorator.AnteHandle` is placed in the ante chain before `SetPubKeyDecorator`/`SigVerificationDecorator`/`IncrementSequenceDecorator`, and after `DeductFeeDecorator` which already skipped fee collection for gasless txs. For a gasless tx from a not-yet-seen signer address, it independently verifies the signature against `account_number=0, sequence=0`, then unconditionally calls `ak.NewAccountWithAddress` + `SetAccount`, and returns `ctx, nil` — short-circuiting the remainder of the ante chain. [5](#0-4) 

Because Cosmos SDK's `BaseApp.runTx` commits the AnteHandler's cache-context state changes independently of, and prior to, message execution, this new account persists in state even if the wrapped message subsequently fails (e.g. `MsgVoteInbound` rejected because the signer is not a bonded Universal Validator). The gasless whitelist includes messages like `MsgExecutePayload`, which by design accepts submission from "any account," and `MsgVoteInbound`/`MsgVoteChainMeta`/etc., whose authorization checks live entirely inside the message handler, not the ante chain. [6](#0-5) 

There is no rate limit, bonding requirement, deposit, or subsequent cleanup/pruning of accounts created this way — unlike every other stateful cache in this codebase (`universalClient/chains/common/event_cleaner.go`, `universalClient/tss/expirysweeper/sweeper.go`), which have explicit TTL-based reaping. The `auth` account store has no equivalent for these zero-value, zero-activity accounts.

### Impact Explanation
An attacker with no Push tokens can flood the network with a large volume of free, valid gasless transactions (only bandwidth/CPU cost to attacker, no chain-side cost), each minting a brand-new permanent state entry in the `auth` module. This grows validator disk and in-memory IAVL/state size indefinitely, increasing resource consumption on every full node and validator that must store and replay this state — directly matching the "Increasing network processing node resource consumption... without brute force actions" impact category, and at scale can degrade block processing time across the network.

### Likelihood Explanation
High. The attack requires only: (1) generating a keypair, (2) constructing a minimal gasless message (e.g. `MsgExecutePayload` or `MsgVoteChainMeta` with any signer), and (3) submitting it — no funds, no special privileges, no validator/UV bonding needed. It can be scripted trivially and repeated with fresh keys indefinitely since `HasAccount` will always be false for a never-used address.

### Recommendation
Do not let `AccountInitDecorator` unconditionally persist new accounts for arbitrary gasless message types. Options: restrict account auto-creation to message types whose handler enforces caller authorization independent of account existence (e.g., only UV-vote messages after confirming the signer is a currently-bonded Universal Validator via a lightweight pre-check in the decorator itself), impose a rate limit / minimum stake or registration requirement before auto-creating accounts, or require the wrapped message to fully succeed before letting the account-creation side effect persist (e.g., don't cache-commit ante-side creation independent of message result for this decorator).

### Proof of Concept
1. Generate a large number of fresh keypairs offline (no funding needed).
2. For each keypair, construct a tx containing a single gasless-whitelisted message (e.g. `MsgExecutePayload`, which explicitly allows submission by "any account") with `account_number=0, sequence=0`, signed correctly.
3. Broadcast repeatedly. `DeductFeeDecorator` skips fee collection (`IsGaslessTx` true); `AccountInitDecorator` detects `!ak.HasAccount`, verifies the self-consistent signature, and calls `ak.SetAccount` — persisting the account regardless of whether `MsgExecutePayload`'s underlying authorization inside the UEA contract subsequently fails.
4. Repeat with new keypairs indefinitely; observe monotonic growth of the `auth` module account count/state size with zero attacker cost.

### Citations

**File:** app/ante/account_init_decorator.go (L31-75)
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
```

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

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** app/ante/ante_cosmos.go (L38-55)
```go
		cosmosante.NewMinGasPriceDecorator(options.FeeMarketKeeper, options.EvmKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		ibcante.NewRedundantRelayDecorator(options.IBCKeeper),
		evmante.NewGasWantedDecorator(options.EvmKeeper, options.FeeMarketKeeper, &feemarketParams),
		// NewAccountInitDecorator must be called before all signature verification decorators and SetPubKeyDecorator
		// - this
		// 1. generates the account for the new accounts only for gasless transactions,
		// 2. verifies the sig, and
		// 3. bypasses the rest of the ante chain
		NewAccountInitDecorator(options.AccountKeeper, options.SignModeHandler),
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
		ante.NewValidateSigCountDecorator(options.AccountKeeper),
		ante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		ante.NewSigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		ante.NewIncrementSequenceDecorator(options.AccountKeeper),
	)
```
