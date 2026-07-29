## Analysis

The external report's bug class is: **an unprivileged, low-cost creation operation lacks any fee, letting an attacker flood the chain with junk state entries.**

Push Chain's native analog is the gasless `AccountInitDecorator` in the ante pipeline, which mints a brand-new on-chain `Account` record for **free** whenever a whitelisted gasless message is submitted from a not-yet-registered address.

### Title
Free, Unbounded On-Chain Account Creation via Gasless Ante Path Enables Spam/State-Bloat DoS - (File: `app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator.AnteHandle` — wired into the ante chain for every gasless message type (`MsgExecutePayload`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`) — creates a new `auth` module `Account` entry the moment it sees a not-yet-registered signer on a gasless tx, requiring only a cheap, attacker-generated Ed25519/secp256k1 keypair and a self-consistent signature over `account_number=0, sequence=0`. No fee is charged (`MinGasPriceDecorator` and `DeductFeeDecorator` both bypass gasless txs entirely), and the decorator short-circuits the ante chain (`return ctx, nil`) before the underlying message handler ever runs. This is the same missing-cost primitive as `CreatePool` in the Elys report: object creation (there, an AMM pool; here, an on-chain account) has no economic cost gate, so an unprivileged party can create unbounded numbers of them.

### Finding Description [1](#0-0) 
The decorator only activates for gasless txs, whose allowlist is defined in [2](#0-1) . Any tx composed solely of these message types (e.g., a syntactically-valid `MsgExecutePayload` targeting a garbage `UniversalAccountId`, with no real UEA, no real payload effect) qualifies.

For a not-yet-registered signer, the decorator: [3](#0-2) 
verifies the signature against a fixed `account_number=0, sequence=0` signer-data tuple, then calls `ak.NewAccountWithAddress` + `ak.SetAccount` and returns immediately — **before** `next(ctx, tx, simulate)` is ever invoked, meaning the message body (e.g., `ExecutePayload`) is never executed and its own validation (chain-enabled checks, UEA signature checks, etc.) never runs.

Both the fee and min-gas-price decorators already skip gasless txs upstream of this: [4](#0-3) [5](#0-4) 

So the full cost to an attacker of causing one new permanent `Account` KV entry is: generate a keypair locally (free), sign one transaction, and broadcast it. There is no bond, no minimum balance, no fee, and no per-address/per-block rate limit visible in this decorator or in `IsGaslessTx`.

### Impact Explanation
This maps to the "gasless admission" pivot in the Push Chain scope: *"gasless allowlisting, authz wrapping, ante checks, first-use account initialization... must not turn attacker input into accepted authorization."* An attacker can:
- Permanently bloat the `x/auth` account KV store with unlimited spam accounts, at zero on-chain cost, purely through ordinary transaction submission (no privileged role required).
- Consume validator CPU on real signature verification for every spam tx (`authsigning.VerifySignature` still runs), and consume block space with zero corresponding fee revenue, degrading throughput available to legitimate gasless traffic (UV votes, real `MsgExecutePayload` calls) — this is an application-layer resource-exhaustion vector, not a raw network-level flood, so it falls inside the allowed DoS scope ("denial of service only when it is not network-level and is reachable without privileged control").
- This is distinct from, and cheaper than, spamming `MsgExecutePayload` all the way through EVM execution (which is gated by `DeductGasFeesFromReceipt` against the UEA's own balance) — the account-init short-circuit bypasses that gate entirely because the message body never executes.

### Likelihood Explanation
High. No special privileges, no bonded-validator status, and no funded account are required — the entire cost is generating a keypair and broadcasting a transaction, which is scriptable and can be automated. The relevant code paths (`IsGaslessTx`, `AccountInitDecorator`) are unconditionally reachable by any external unprivileged submitter of a normal transaction.

### Recommendation
Introduce an economic or rate-limiting gate on first-time account creation via the gasless path, analogous to the pool-creation-fee fix referenced in the external report:
- Require a minimum bonded-UV check (or other authorization) before allowing gasless-path account auto-creation for message types not intrinsically tied to a bonded validator (e.g., `MsgExecutePayload` from arbitrary addresses), or
- Apply a per-IP/per-block/per-signer rate limit or proof-of-work-style throttle specifically on the account-creation branch of `AccountInitDecorator`, or
- Charge a minimal, non-refundable creation cost (deducted from the newly created account once funded, or billed to a fee-payer) for the account-init branch specifically, separate from the general gasless fee exemption meant for legitimate UV/payload traffic.

### Proof of Concept
1. Generate N fresh keypairs locally (free, off-chain).
2. For each keypair, construct a `MsgExecutePayload` with an arbitrary/garbage `UniversalAccountId` and `UniversalPayload` (content is irrelevant — the message body never executes).
3. Sign each tx using `account_number=0, sequence=0` per [6](#0-5) .
4. Broadcast all N transactions.
5. Observe: N new `Account` entries are created in `x/auth` state, none of the N txs paid any fee (`IsGaslessTx` true), and none of the N `MsgExecutePayload` handlers actually ran (ante short-circuits with `return ctx, nil` at line 74). [7](#0-6)  confirms the existing test suite only validates the non-gasless bypass path, not any rate/cost limiting on the account-creation branch itself.

### Citations

**File:** app/ante/account_init_decorator.go (L31-36)
```go
func (aid AccountInitDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	if !txpolicy.IsGaslessTx(tx) {
		// Skip account initialization for non-gasless transactions
		ctx.Logger().Debug("account init decorator: non-gasless tx, skipping account init")
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

**File:** app/ante/account_init_decorator.go (L113-131)
```go
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

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```

**File:** app/ante/account_init_decorator_test.go (L16-37)
```go
// TestAccountInitDecorator_NonGaslessTxPassesThrough verifies that the decorator
// immediately calls next for non-gasless transactions (those not in the allowed
// gasless message type list).
func TestAccountInitDecorator_NonGaslessTxPassesThrough(t *testing.T) {
	ak := newMockAccountKeeperAnte(sdk.AccAddress([]byte("feeCollector")))
	aid := ante.NewAccountInitDecorator(ak, nil /*signModeHandler not needed for non-gasless*/)

	// banktypes.MsgSend is not gasless.
	tx := mockFeeTx{
		msgs: []sdk.Msg{&banktypes.MsgSend{}},
	}

	ctx := newAnteTestCtx(t, false)
	nextCalled := false
	_, err := aid.AnteHandle(ctx, tx, false, func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
		nextCalled = true
		return ctx, nil
	})

	require.NoError(t, err)
	require.True(t, nextCalled, "next handler must be called for non-gasless tx")
}
```
