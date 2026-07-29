### Title
Unprivileged, cost-free mass on-chain account creation via the gasless `AccountInitDecorator` enables unbounded state-bloat / mempool-spam DoS - (`File: app/ante/account_init_decorator.go`)

### Summary
The Nouns bug is about a spam-protection mechanism that is keyed to a mutable identity (the proposer *address*) rather than the scarce resource it is meant to protect (the Noun), letting an attacker cheaply mint unlimited new "protected" identities and grief the protocol. Push Chain's `AccountInitDecorator` has the same structural flaw applied to on-chain account creation: it creates a brand-new, permanently-stored account for *any* address that signs a gasless transaction, with no economic cost and no check that the signer is an actual Universal Validator or authorized party.

### Finding Description
`AccountInitDecorator.AnteHandle` [1](#0-0)  fires for any transaction whose messages are all in the gasless allowlist (`MsgExecutePayload`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`) [2](#0-1) . The decorator only checks:
1. The tx is gasless (message-type based, not signer-based).
2. There is exactly one signer.
3. That signer's account does not yet exist on chain.

If all three hold, it verifies the signature against a fixed `account_number=0, sequence=0` [3](#0-2) , creates the account, sets `sequence=1`, and **short-circuits the rest of the ante chain** — skipping fee deduction, min-gas-price checks, and normal signature/sequence verification for that tx [4](#0-3) .

Crucially, nothing here checks that the signer is a bonded Universal Validator, holds an `authz` grant, or is otherwise privileged to submit `MsgVoteInbound`/`MsgVoteOutbound`/etc. — that authorization is enforced later, inside the message handler, *after* the ante chain (and thus account creation) has already run. In the standard Cosmos SDK `runTx` flow, the AnteHandler executes in its own cache-context that is `Write()`-committed **before** `runMsgs` executes; if the message handler subsequently rejects the message (e.g. "not a registered validator", "invalid verificationData", "chain inbound disabled"), only the message-execution branch is rolled back — the AnteHandler's committed state (the newly created account) is not undone.

This means an attacker with no PC balance, no stake, and no UV/authz grant can:
1. Generate an arbitrary throwaway ECDSA keypair.
2. Craft a syntactically-valid `MsgExecutePayload` (or `MsgVoteInbound`, etc.) naming that keypair's address as sole signer, with `account_number=0, sequence=0`.
3. Sign it correctly (trivial — it's their own key).
4. Submit it. `AccountInitDecorator` creates and persists a permanent new `BaseAccount` in state, at zero fee (gasless) and without any economic bond, before the message content is ever validated.
5. Repeat with a fresh keypair indefinitely.

This is directly analogous to the Nouns issue: a protection/bootstrap mechanism intended for a narrow, legitimate case (a freshly-keygen'd UV hot key voting for the first time) [5](#0-4)  is keyed on "does this specific address already have an account," a condition any attacker can trivially reset by generating a new address — just as Nouns' `checkNoActiveProp` was keyed on proposer address, which an attacker could reset by moving the Noun to a new address.

### Impact Explanation
Each spam transaction permanently grows the `x/auth` account store at zero cost to the attacker (no gas fee, no stake, no bonded status required), and consumes mempool/CheckTx/DeliverTx CPU cycles and block space for every validator that must process it. This is an unprivileged, non-network-level denial-of-service / resource-exhaustion vector reachable purely through ordinary transaction submission — explicitly in scope per the allowed-impact gate ("denial of service only when it is not network-level and is reachable without privileged control").

### Likelihood Explanation
High feasibility: no special access, funds, or validator status is required. An attacker only needs to be able to generate keypairs and sign a well-formed transaction of a gasless message type — something any external, unauthenticated party can do. The cost per spam unit is effectively zero (no gas fee under `gasless=true`, no minimum-gas-price check), so the attack scales linearly with attacker bandwidth/compute, not with any economic barrier.

### Recommendation
- Do not create/persist a full on-chain account purely from an unauthenticated first-seen signature. Gate `AccountInitDecorator`'s account-creation short-circuit on the actual authorization the underlying message requires (e.g., only allow it when the signer is present in `UniversalValidatorSet` for vote-type messages, or has a legitimate pre-registered claim), rather than allowing it for any gasless message type from any address.
- Alternatively, require the message-level authorization check to run (or be pre-validated) before the account-creation side effect is committed, so an unauthorized signer's account creation does not survive a subsequent message-level rejection.
- Consider a small non-refundable economic cost (e.g., minimum stake/bond check, rate limiting per IP/session at the mempool layer, or requiring a valid `authz` grant from an existing bonded validator) before permitting first-use account bootstrap via the gasless path.

### Proof of Concept
1. Generate keypair `K1`, derive `push1...` address `A1` (no funds, no account, no UV registration).
2. Build a `MsgExecutePayload` (or `MsgVoteInbound`) with `Signer = A1`, arbitrary/garbage `UniversalAccountId`/payload fields.
3. Sign with `account_number=0, sequence=0` using `K1` and submit via `broadcast-tx`.
4. Observe: `AccountInitDecorator` verifies the signature, calls `NewAccountWithAddress`/`SetAccount` for `A1`, and short-circuits the ante chain — no fee charged, no UV/authz check performed [4](#0-3) .
5. Even if the underlying `ExecutePayload`/`VoteInbound` handler later rejects the message (invalid `UniversalAccountId`, not a UV, chain disabled, etc.), the account for `A1` remains permanently in state because the ante-chain commit already happened independently of message execution success.
6. Repeat steps 1–5 with fresh keypairs `K2, K3, ... Kn` at arbitrarily high volume and zero marginal cost.

### Citations

**File:** app/ante/account_init_decorator.go (L31-50)
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

**File:** app/ante/account_init_decorator.go (L106-143)
```go
	newAccAddr := sdk.AccAddress(signers[0])
	for _, sig := range sigs {
		pubKey := sig.PubKey
		if pubKey == nil {
			return errorsmod.Wrap(sdkerrors.ErrInvalidPubKey, "pubkey is not provided in signature")
		}

		// retrieve signer data
		chainID := ctx.ChainID()
		var accSequence uint64 = 0
		var accNum uint64 = 0

		// no need to verify signatures on recheck tx
		if !simulate && !ctx.IsReCheckTx() && ctx.IsSigverifyTx() {
			anyPk, _ := codectypes.NewAnyWithValue(pubKey)

			signerData := txsigning.SignerData{
				Address:       newAccAddr.String(),
				ChainID:       chainID,
				AccountNumber: accNum,
				Sequence:      accSequence,
				PubKey: &anypb.Any{
					TypeUrl: anyPk.TypeUrl,
					Value:   anyPk.Value,
				},
			}
			adaptableTx, ok := tx.(authsigning.V2AdaptableTx)
			if !ok {
				return fmt.Errorf("expected tx to implement V2AdaptableTx, got %T", tx)
			}
			txData := adaptableTx.GetSigningTxData()
			ctx.Logger().Debug("account init decorator: verifying signature for new account",
				"address", newAccAddr.String(),
				"chain_id", chainID,
				"acc_num", accNum,
				"sequence", accSequence,
			)
			err = authsigning.VerifySignature(ctx, pubKey, signerData, sig.Data, aid.signModeHandler, txData)
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

**File:** app/README.md (L180-182)
```markdown
| `AccountInitDecorator` | `app/ante/account_init_decorator.go` | If signer has no on-chain account yet, creates it mid-pipeline with `account_number=0, sequence=0`, verifies the signature against those values, and short-circuits the rest of the ante chain |

The third decorator is what lets a freshly-keygen'd Universal Validator hot key vote on its very first tx, without anyone first having to fund it.
```
