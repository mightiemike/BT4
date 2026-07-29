## Finding: Free, unprivileged spam of `MsgExecutePayload` triggers real EVM execution at zero cost

### Title
Gasless, permission-less `MsgExecutePayload` lets any unfunded attacker force repeated EVM execution against real UEAs for free - ([File: x/uexecutor/keeper/msg_execute_payload.go])

### Summary
The external report's root cause is Cosmos-SDK/consensus-layer messages that require no funds from the sender but still force full CL/validator processing, enabling costless spam. Push Chain has a native analog: `MsgExecutePayload` is whitelisted as gasless and callable by "any" signer (not just bonded Universal Validators), and its handler performs real EVM work (UEA address resolution and, for already-deployed UEAs, a full `executeUniversalTx` EVM call) before any signature check fails. Because fee deduction and minimum-gas-price checks are both skipped for gasless transactions, an attacker never pays anything to trigger this work, regardless of how many times they repeat it.

### Finding Description
`app/txpolicy/gasless.go` whitelists `MsgExecutePayload` as a gasless message type: [1](#0-0) 

`app/ante/fee.go`'s `DeductFeeDecorator` and `app/cosmos/min_gas_price.go`'s `MinGasPriceDecorator` both unconditionally skip fee/fee-price enforcement for any tx classified as gasless: [2](#0-1) [3](#0-2) 

Unlike `MsgVoteInbound`/`MsgVoteOutbound`/`MsgVoteChainMeta`, which are gated by `IsBondedUniversalValidator`/`IsTombstonedUniversalValidator` checks in the message server, `MsgExecutePayload` has no such gate — the module README explicitly documents its authority as `any`: [4](#0-3) 

The handler `Keeper.ExecutePayload` resolves the UEA address via an EVM call and, critically, only requires a non-zero balance check when the UEA is **not yet deployed** — for any already-deployed UEA (i.e., any real, previously used UEA on the chain) it proceeds straight to executing the payload with no balance precondition at all: [5](#0-4) 

`ExecutePayloadV2` then performs a real `CallUEAExecutePayload` EVM call (full EVM execution against the UEA contract) before any fee accounting is attempted; fee deduction — even when it succeeds — is charged to the UEA (`ueaAddr`), never to the attacker's signer: [6](#0-5) 

As the module README states, the actual cryptographic authorization check happens *inside* the UEA contract's `executeUniversalTx`, meaning the EVM call — and the associated computation/state work — always runs before an invalid `VerificationData` causes a revert: [7](#0-6) 

Put together: an attacker with a freshly generated key and zero token balance can (1) get a Cosmos account auto-created mid-ante via `AccountInitDecorator` for gasless txs, then (2) repeatedly submit `MsgExecutePayload` targeting any existing deployed UEA with garbage `VerificationData`, forcing the chain to run `CallFactoryToGetUEAAddressForOrigin` and `CallUEAExecutePayload` on every submission — with fee deduction skipped and no fee ever coming from the attacker's own balance.

### Impact Explanation
This lets an unprivileged, fund-less attacker repeatedly force real EVM computation (contract calls, potential storage reads) on the core validator set at zero cost, for as many transactions as they can get into blocks — the same "spam the CL/consensus layer via a fee-free message" pattern as the original Story Protocol report. This is a denial-of-service class impact reachable without any privileged role (no bonding, no funds, no admin/governance action needed), matching the in-scope "denial of service...not network-level and...reachable without privileged control" criterion.

### Likelihood Explanation
High: the attack requires only generating a key (free), and the whitelist/skip logic is unconditional for any tx composed entirely of gasless message types — no additional gate (e.g., minimum bonded stake, minimum balance, rate limit) exists specifically for `MsgExecutePayload` senders, unlike the vote messages which are UV-bonded-gated.

### Recommendation
Add a spam-resistant gate to `MsgExecutePayload` (and any other "any signer, gasless" message) — e.g., require the signer to be the UEA owner (or hold a minimum balance), enforce a per-signer/per-UEA rate limit, or require actual fee payment from the tx signer (not the UEA) for `MsgExecutePayload`/`MsgMigrateUEA`-style entry points before performing the EVM call, mirroring how vote messages are restricted to bonded, non-tombstoned Universal Validators.

### Proof of Concept
1. Generate an arbitrary new keypair/account (no funds needed).
2. Submit `MsgExecutePayload` with that account as `Signer`, targeting any known deployed UEA (`UniversalAccountId`) with a syntactically valid but cryptographically bogus `VerificationData`.
3. Ante pipeline: `MinGasPriceDecorator` and `DeductFeeDecorator` both skip enforcement (gasless whitelist match); `AccountInitDecorator` creates the account on first use.
4. Message handler: `ExecutePayload` resolves the deployed UEA (no balance check since it's already deployed) and calls `CallUEAExecutePayload`, running full EVM execution before the UEA contract reverts on bad signature.
5. Repeat indefinitely from the same or new zero-balance accounts — no fee is ever paid, no privilege is ever required.

### Citations

**File:** app/txpolicy/gasless.go (L16-25)
```go
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

**File:** x/uexecutor/README.md (L199-207)
```markdown
| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |

> **UEA migration is now part of payload execution.** There used to be a separate `MsgMigrateUEA` message; that path has been removed. UEAs are upgraded by submitting a normal `MsgExecutePayload` whose payload calls the UEA's migration entry point on the EVM side. The Cosmos layer no longer has a dedicated migration message — the UEA contract is the source of truth for who is allowed to migrate it and to what implementation.
```

**File:** x/uexecutor/README.md (L220-237)
```markdown
#### Where authorization actually lives

The cryptographic binding is enforced inside the UEA contract's `executeUniversalTx` (see [`UEA_EVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_EVM.sol#L145) and [`UEA_SVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_SVM.sol)):

1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**

#### Why this is safe under `Signer ≠ Owner`

An attacker submitting `MsgExecutePayload` with their own `Signer` and a victim's `UniversalAccountId` produces no exploitable outcome:

- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
- The attacker cannot forge `VerificationData` that recovers to the victim's owner key.
- The contract reverts → the keeper returns an error → the Cosmos transaction reverts in full.
- Net effect: zero state change. No EVM gas is charged to the victim UEA (the deduction is rolled back with the rest of the transaction). The submission costs the attacker nothing on chain (gasless), but also achieves nothing.
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L48-67)
```go
	factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

	// Step 2: Compute smart account address
	// Calling factory contract to compute the UEA address
	ueaAddr, isDeployed, err := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, evmFrom, factoryAddress, universalAccountId)
	if err != nil {
		return err
	}

	if !isDeployed {
		// only deploy if the UEA address has funds and not deployed yet
		ueaAccAddr := sdk.AccAddress(ueaAddr.Bytes())
		balance := k.bankKeeper.GetBalance(sdkCtx, ueaAccAddr, pchaintypes.BaseDenom)
		if balance.Amount.Sign() == 0 {
			k.Logger().Warn("execute payload rejected: UEA not deployed and has no balance",
				"chain", caip2Identifier,
				"owner", universalAccountId.Owner,
			)
			return fmt.Errorf("UEA is not deployed")
		}
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-53)
```go
	// Step 2: Wrap EVM execution + fee deduction in a CacheContext so they
	// commit/revert together. If fee deduction fails, the EVM state changes
	// from CallUEAExecutePayload are discarded — closes the free-execution
	// gap when the UEA has no native UPC to cover gas.
	cacheCtx, writeCache := sdkCtx.CacheContext()
	receipt, execErr := k.CallUEAExecutePayload(cacheCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 3: Try fee deduction in the same cache. DeductGasFeesFromReceipt
	// is a no-op if the receipt is nil or GasUsed == 0 (EVM call produced
	// nothing to bill).
	if feeErr := k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		// Cache discarded — EVM state and any partial fee work both roll back.
		return receipt, fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```
