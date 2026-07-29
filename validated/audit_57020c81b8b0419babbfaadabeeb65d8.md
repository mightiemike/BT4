### Title
Unlimited, cost-free on-chain account creation via `AccountInitDecorator` for any gasless message type — ([File: app/ante/account_init_decorator.go])

### Summary
The external report describes an "eternal free mint" bug: a missing counter/restriction lets a user repeat a privileged free action indefinitely, at zero cost, bypassing the business logic that should gate it. The Push Chain analog is `AccountInitDecorator`, which silently creates a brand-new on-chain `BaseAccount` for *any* address that signs a gasless transaction, with no limit on how many times an attacker may do this and at zero fee.

### Finding Description
Push Chain's gasless pipeline whitelists several message types, including `MsgExecutePayload`, which any (unpermissioned) account may submit and pay no fee for: [1](#0-0) [2](#0-1) 

For any gasless tx, `AccountInitDecorator.AnteHandle` checks whether the sole signer already has an account. If not, it verifies a self-signature (trivially satisfiable — the attacker generates a fresh keypair and signs their own transaction) and then unconditionally persists a new account via `SetAccount`, before the rest of the ante chain (fee deduction, gas checks) or the actual message logic ever runs: [3](#0-2) 

The signature check in `verifySignatureForNewAccount` only proves the attacker controls the new key — it imposes no cost, no rate limit, and no cap on the number of distinct fresh accounts a single attacker can create this way: [4](#0-3) 

Because ante-handler state mutations commit independently of whether the wrapped message ultimately succeeds (standard Cosmos SDK `runTx` semantics — the ante branch is committed even if the message execution branch later fails/reverts), the account is persisted on-chain regardless of whether the accompanying `MsgExecutePayload` payload is valid, well-formed, or ever executes successfully. `DeductFeeDecorator` also skips fee collection entirely for gasless txs: [5](#0-4) 

The net effect mirrors the reported bug class precisely: there is no function or check anywhere in this path to prevent an unprivileged user from repeating this "free mint" (of on-chain account state) indefinitely — exactly the "no limit on the limit" condition called out in the original report.

### Impact Explanation
An attacker can generate an unbounded number of fresh keypairs off-chain for free, sign one `MsgExecutePayload` per keypair (the payload content and `UniversalAccountId` can be garbage since the account gets created in the ante stage before payload validation/execution), and broadcast them. Every such transaction persists a new `BaseAccount` in state at zero fee and zero gas cost to the attacker. This is an unprivileged, unbounded, cost-free state-growth vector — a denial-of-service class impact (non-network-level, reachable without any privileged role) explicitly listed as in-scope in the allowed impact gate.

### Likelihood Explanation
High. No special access, stake, validator bonding, or capital is required — only the ability to generate keypairs and sign a syntactically valid Cosmos transaction, which is free and requires no interaction with any other party.

### Recommendation
Add a rate limit / cost / cap on ante-level account creation for gasless transactions — e.g., require the created account to be provably tied to a legitimate UEA/CEA derivation (rather than an arbitrary attacker-chosen key), impose a minimum per-block or per-address creation quota, or require the wrapped message to be validated (or at least stateless-validated) before the account-creation side effect is allowed to persist.

### Proof of Concept
1. Generate a fresh Cosmos keypair `K1` with address `A1` (free, offline).
2. Construct a `MsgExecutePayload` with `Signer = A1` and any `UniversalAccountId`/payload content (does not need to be valid — validation of the payload happens later in `ExecutePayload`, after account creation already occurred in the ante stage).
3. Sign the transaction with `K1` using account_number=0, sequence=0 (satisfies `verifySignatureForNewAccount`).
4. Broadcast. `AccountInitDecorator` creates and persists `BaseAccount(A1)` for free; the underlying `MsgExecutePayload` may subsequently fail (e.g. invalid payload), but the account remains on-chain.
5. Repeat steps 1–4 with `K2, K3, …, Kn` indefinitely — no counter, cost, or cap prevents unbounded repetition.

### Citations

**File:** app/txpolicy/gasless.go (L17-25)
```go
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
```

**File:** x/uexecutor/README.md (L199-215)
```markdown
| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |

> **UEA migration is now part of payload execution.** There used to be a separate `MsgMigrateUEA` message; that path has been removed. UEAs are upgraded by submitting a normal `MsgExecutePayload` whose payload calls the UEA's migration entry point on the EVM side. The Cosmos layer no longer has a dedicated migration message — the UEA contract is the source of truth for who is allowed to migrate it and to what implementation.

Vote messages check `IsBondedUniversalValidator` and `IsTombstonedUniversalValidator` on `x/uvalidator` before accepting the vote. Tombstoned validators are silently rejected.

### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
```

**File:** app/ante/account_init_decorator.go (L52-74)
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
```

**File:** app/ante/account_init_decorator.go (L83-169)
```go
func (aid AccountInitDecorator) verifySignatureForNewAccount(ctx sdk.Context, tx sdk.Tx, simulate bool) error {
	sigTx, ok := tx.(authsigning.Tx)
	if !ok {
		return errorsmod.Wrap(sdkerrors.ErrTxDecode, "invalid transaction type")
	}

	// stdSigs contains the sequence number, account number, and signatures.
	// When simulating, this would just be a 0-length slice.
	sigs, err := sigTx.GetSignaturesV2()
	if err != nil {
		return err
	}

	signers, err := sigTx.GetSigners()
	if err != nil {
		return err
	}

	// check that signer length and signature length are the same
	if len(sigs) != len(signers) {
		return errorsmod.Wrapf(sdkerrors.ErrUnauthorized, "invalid number of signer;  expected: %d, got %d", len(signers), len(sigs))
	}

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
			if err != nil {
				var errMsg string
				if OnlyLegacyAminoSigners(sig.Data) {
					// If all signers are using SIGN_MODE_LEGACY_AMINO, we rely on VerifySignature to check account sequence number,
					// and therefore communicate sequence number as a potential cause of error.
					errMsg = fmt.Sprintf("signature verification failed; please verify account number (%d), sequence (%d) and chain-id (%s)", accNum, accSequence, chainID)
				} else {
					errMsg = fmt.Sprintf("signature verification failed; please verify account number (%d) and chain-id (%s): (%s)", accNum, chainID, err.Error())
				}
				ctx.Logger().Debug("account init decorator: signature invalid for new account",
					"address", newAccAddr.String(),
					"chain_id", chainID,
				)
				return errorsmod.Wrap(sdkerrors.ErrUnauthorized, errMsg)

			}
		} else {
			ctx.Logger().Debug("account init decorator: skipping signature verification",
				"address", newAccAddr.String(),
				"simulate", simulate,
				"is_recheck_tx", ctx.IsReCheckTx(),
				"is_sigverify_tx", ctx.IsSigverifyTx(),
			)
		}
	}
	return nil
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
