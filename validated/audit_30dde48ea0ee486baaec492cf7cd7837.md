### Title
Unbounded free account creation via gasless `MsgExecutePayload` allows unprivileged store-bloat DoS - ([File: app/ante/account_init_decorator.go])

### Summary
The external report's core defect is an unvalidated, attacker-controlled value being used to satisfy a security-relevant precondition (a host restriction) with no upstream authentication or cost. The Push Chain analog is `AccountInitDecorator.AnteHandle`, which lets *any* unprivileged signer, for any of the gasless message types in `app/txpolicy/gasless.go` (most notably `MsgExecutePayload`, which "any" account may submit per `x/uexecutor/README.md:204,215`), create a brand-new on-chain account for zero fee, with no rate limiting, no funding requirement, and no cost tied to the number of distinct signer keys used.

### Finding Description
`IsGaslessTx` (`app/txpolicy/gasless.go:14-49`) whitelists `MsgExecutePayload` (along with vote messages) as gasless. `AccountInitDecorator.AnteHandle` (`app/ante/account_init_decorator.go:31-81`) is invoked for every gasless tx: [1](#0-0) 

If the signer address has no existing account (`aid.ak.HasAccount`), the decorator verifies only a signature over `account_number=0, sequence=0` (trivial for any keypair the attacker controls, since these are the default values for a genuinely new account) and then unconditionally calls `aid.ak.NewAccountWithAddress` + `SetAccount`, persisting a new account record to the auth store — before any fee is charged, before the underlying message is executed, and independent of whether that message will later succeed.

Because `MsgExecutePayload` is explicitly designed to be submittable by "any" account (`x/uexecutor/README.md:211-218`, "Any account may submit the message"), and because the ante pipeline short-circuits fee/gas checks for gasless txs (`DeductFeeDecorator`, `MinGasPriceDecorator` per `app/README.md:174-182`), an attacker can generate an arbitrary number of fresh keypairs and, for each one, submit a single `MsgExecutePayload` (with any syntactically valid but semantically arbitrary `UniversalAccountId`/payload — actual authorization is enforced later inside the UEA contract, not by the chain, so the message needs no real ownership to pass `ValidateBasic`) to force the chain to create and persist a new account entry at no cost.

### Impact Explanation
This is unbounded, cost-free account/state growth driven entirely by unprivileged external input — a direct parallel to the reported bug class (unvalidated user input bypassing an intended restriction, "used to launch a distributed denial-of-service attack"). Every fabricated account permanently occupies space in the `x/auth` KV store; there is no bond, minimum balance, nonce cost, or per-block/per-IP throttle preventing mass creation. Sustained abuse inflates chain state size, increases IAVL/store overhead, and degrades node sync/snapshot/pruning performance over time — a reachable, non-network-level DoS vector satisfying the "denial of service...reachable without privileged control" impact category.

### Likelihood Explanation
High. No privileged role, validator bonding, or governance action is required — only the ability to generate Ed25519/secp256k1 keypairs (free) and broadcast transactions (gasless, so no PC token balance is needed either). The `MsgExecutePayload` message is explicitly documented as callable by "any" signer, and the ante-level account creation is unconditional once `HasAccount` returns false, regardless of the eventual message-execution outcome.

### Recommendation
Short term, do not let account creation for previously-unseen signers be entirely free: require either (a) a minimal collateral/fee even for the first gasless tx from a new account, (b) rate-limiting/cool-down per new-account creation at the node/mempool level, or (c) restricting which gasless message types are eligible to trigger `AccountInitDecorator`'s auto-creation path to those that cannot be trivially replayed by throwaway keys (e.g., only UV-bonded vote messages, which already require a registered validator identity, rather than the open `MsgExecutePayload`). Longer term, consider charging a small non-refundable state-rent-style fee for first-time account creation regardless of gasless status, mirroring how the ACL report recommended requiring a concrete, non-bypassable value rather than trusting unauthenticated attacker input.

### Proof of Concept
1. Generate N distinct keypairs (e.g., secp256k1) off-chain — free, no on-chain interaction needed.
2. For each keypair, construct a `MsgExecutePayload` with an arbitrary/garbage `UniversalAccountId.Owner` and a syntactically valid but non-functional `UniversalPayload` (it does not need to reference a real UEA to pass `ValidateBasic`/ante — actual authorization is enforced only inside the target UEA contract at execution time).
3. Wrap each `MsgExecutePayload` in a tx signed only by the throwaway keypair, using `account_number=0, sequence=0` and zero fee (gasless, per `app/txpolicy/gasless.go`).
4. Broadcast all N transactions. `AccountInitDecorator` will, for each unique signer, verify the trivial account_number/sequence-0 signature and call `NewAccountWithAddress`/`SetAccount`, persisting N new accounts to state — even though the subsequent `MsgExecutePayload` execution will fail/revert on the fake UEA target.
5. Repeat indefinitely with more throwaway keys to observe unbounded, cost-free growth of the `x/auth` account store.

Note: I was not able to fully verify `x/uexecutor/types/msg_execute_payload.go`'s `ValidateBasic` implementation before the tool budget was exhausted, so the exact minimal-viable payload fields required to pass `ValidateBasic` (and thus reach the `AccountInitDecorator`) are unconfirmed — this should be checked to finalize the concrete PoC payload shape, but the account-creation logic itself (`app/ante/account_init_decorator.go:52-75`) and the gasless/any-signer eligibility of `MsgExecutePayload` (`x/uexecutor/README.md:204-218`) are directly confirmed from the indexed code.

### Citations

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
