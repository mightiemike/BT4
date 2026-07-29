Found a valid analog: `MigrationPayload.Migration` (the UEA's new implementation/migration target address) is validated only as a well-formed hex address, with no check that it points to a deployed contract, mirroring the KintoWallet `_recoverer` EOA issue.

### Title
`MigrateUEA` accepts an EOA as the `migration` target with no contract-code check - (File: `x/uexecutor/types/migration_payload.go`)

### Summary
`MsgMigrateUEA` → `Keeper.MigrateUEA` → `CallUEAMigrateUEA` forwards a user-supplied `MigrationPayload.Migration` address straight to the UEA's `migrateUEA` entry point via `DerivedEVMCall`, with the Cosmos-side validation limited to hex-format checking. Nothing in the scoped node code verifies that `Migration` is a deployed contract before submitting the migration transaction.

### Finding Description
`MigrationPayload.ValidateBasic()` only checks that `Migration` is non-empty and a syntactically valid hex address: [1](#0-0) 

This validated payload flows unchanged through `Keeper.MigrateUEA`, which parses the payload, looks up the UEA, and calls `CallUEAMigrateUEA` with the raw `migration_payload.Migration` address embedded in the ABI-encoded call: [2](#0-1) [3](#0-2) 

Unlike `x/uregistry`'s system-contract deployment path, which explicitly guards against mistaking an EOA for a contract using `isContractDeployed` (added specifically to close finding F-2026-17025, distinguishing the EVM `EmptyCodeHash` sentinel from real contract code): [4](#0-3) 

the `x/uexecutor` migration-payload validation path has no equivalent `GetCodeHash`/`isContractDeployed` check before submitting `Migration` as the new implementation target. If a user (or a party crafting the payload signed by the UEA owner) mistakenly supplies an EOA address as `migration`, the Cosmos-layer code will happily submit the transaction to the UEA contract without ever confirming code exists at that address.

### Impact Explanation
Per `x/uexecutor/README.md`, UEA upgrades are performed exclusively through this payload-based migration flow (the standalone message path is documented as removed, though the RPC/keeper code paths clearly remain live and reachable): [5](#0-4) 

If the UEA contract's own `migrateUEA`/UUPS upgrade logic does not itself reject non-contract implementation addresses (that logic lives in `push-chain-core-contracts`, outside this repo's scope), a successful call with an EOA `migration` target would point the proxy's implementation slot at an address with no code. Because migration is described as the only supported upgrade path for a UEA, this would permanently break `executeUniversalTx`/future payload execution for that account — an irreversible loss of control/functionality analogous to the original `recoverer`-as-EOA bug (the account owner is locked out of the very mechanism meant to remedy or upgrade the account). This maps to the "permanent freezing of user...funds" / "unauthorized state transitions in universal execution flows" impact categories for this scope, contingent on the contract-side guard being absent or bypassable.

### Likelihood Explanation
Low-to-moderate. The signature check inside the UEA contract (verified against `verificationData`) still requires the legitimate owner (or someone possessing a valid signature over the migration payload) to have authorized the specific `Migration` address, so an unprivileged third-party attacker cannot unilaterally redirect someone else's UEA. The realistic trigger is user/client-side error (a malformed payload, wrong address copy-paste, or a compromised front-end proposing a bad `migration` value) that the node itself does no sanity-checking to prevent, exactly the scenario the original KintoWallet report flags. Whether this is actually exploitable end-to-end depends on validation inside `UEA_EVM.sol`/`UEA_SVM.sol` (out of this repo), which I could not inspect from this codebase.

### Recommendation
Add a code-presence check (mirroring `isContractDeployed` in `x/uregistry/keeper/genesis.go`) on `MigrationPayload.Migration` before constructing/submitting the `MigrateUEA` derived EVM call in `x/uexecutor/keeper/msg_migrate_uea.go` — e.g. call `k.evmKeeper.GetCodeHash(ctx, migrationAddr)` and reject the request (returning an error rather than emitting the on-chain tx) if the address has no code (`EmptyCodeHash` or nil).

### Proof of Concept
1. Owner (or a compromised client acting on the owner's behalf) constructs a `MsgMigrateUEA` with `MigrationPayload.Migration` set to a valid-format but code-less EOA address, plus a validly-computed signature over that payload.
2. `MigrationPayload.ValidateBasic()` passes (format-only check) — [1](#0-0) .
3. `Keeper.MigrateUEA` proceeds to call `CallUEAMigrateUEA`, submitting the migration on-chain with no code-presence check on `Migration` — [6](#0-5) .
4. If the UEA's on-chain upgrade logic accepts the call, the UEA's implementation now points at a codeless address, bricking all subsequent `executeUniversalTx` calls for that account.

Note: I could not access `UEA_EVM.sol`'s `migrateUEA` implementation (lives in the separate `push-chain-core-contracts` repo) to confirm whether it independently guards against a codeless implementation address — this is the key unresolved dependency for confirming full exploitability.

### Citations

**File:** x/uexecutor/types/migration_payload.go (L24-31)
```go
func (p MigrationPayload) ValidateBasic() error {
	// Validate 'migration' address
	if strings.TrimSpace(p.Migration) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "migration address cannot be empty")
	}
	if !utils.IsValidAddress(p.Migration, utils.HEX) {
		return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid migration contract address format: %s", p.Migration)
	}
```

**File:** x/uexecutor/keeper/msg_migrate_uea.go (L15-74)
```go
func (k Keeper) MigrateUEA(ctx context.Context, evmFrom common.Address, universalAccountId *types.UniversalAccountId, migrationPayload *types.MigrationPayload, signature string) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	// Get Caip2Identifier for the universal account
	caip2Identifier := universalAccountId.GetCAIP2()

	k.Logger().Info("migrate UEA",
		"from", evmFrom.Hex(),
		"chain", caip2Identifier,
		"owner", universalAccountId.Owner,
	)

	// Step 1: Parse and validate payload and signature
	_, err := types.NewAbiMigrationPayload(migrationPayload)
	if err != nil {
		return errors.Wrapf(err, "invalid migration payload")
	}

	// add signature verification
	signatureVal, err := utils.HexToBytes(signature)
	if err != nil {
		return errors.Wrapf(err, "invalid signature format")
	}

	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}

	// TODO: Decide later if migration should be disabled if inbound is disabled
	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("migrate UEA rejected: chain not enabled", "chain", caip2Identifier)
		return fmt.Errorf("chain %s is not enabled", caip2Identifier)
	}

	factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

	// Step 2: Compute smart account address
	// Calling factory contract to compute the UEA address
	ueaAddr, isDeployed, err := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, evmFrom, factoryAddress, universalAccountId)
	if err != nil {
		return err
	}

	if !isDeployed {
		k.Logger().Warn("migrate UEA rejected: UEA not deployed", "chain", caip2Identifier, "owner", universalAccountId.Owner)
		return fmt.Errorf("UEA is not deployed")
	}

	k.Logger().Debug("migrating UEA",
		"uea", ueaAddr.Hex(),
		"chain", caip2Identifier,
		"from", evmFrom.Hex(),
	)

	// Step 3: Migrate UEA through UEA
	receipt, err := k.CallUEAMigrateUEA(sdkCtx, evmFrom, ueaAddr, migrationPayload, signatureVal)
	if err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/evm.go (L196-227)
```go
func (k Keeper) CallUEAMigrateUEA(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	migration_payload *types.MigrationPayload,
	signature []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiMigrationPayload, err := types.NewAbiMigrationPayload(migration_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		nil,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"migrateUEA",
		abiMigrationPayload,
		signature,
	)
}
```

**File:** x/uregistry/keeper/genesis.go (L136-151)
```go
// isContractDeployed reports whether addr already holds executable EVM code.
// EOAs in cosmos/evm carry the keccak256-of-empty-bytes sentinel, so a
// length-only check would treat any touched EOA as a deployed contract and
// silently skip the deploy sequence for that slot (F-2026-17025). Compare
// against the empty-code-hash sentinel via Account.HasCodeHash instead.
func isContractDeployed(
	ctx sdk.Context,
	evmKeeper types.EVMKeeper,
	addr common.Address,
) bool {
	acc := evmKeeper.GetAccount(ctx, addr)
	if acc == nil || len(acc.CodeHash) == 0 {
		return false
	}
	return acc.HasCodeHash()
}
```

**File:** x/uexecutor/README.md (L207-207)
```markdown
> **UEA migration is now part of payload execution.** There used to be a separate `MsgMigrateUEA` message; that path has been removed. UEAs are upgraded by submitting a normal `MsgExecutePayload` whose payload calls the UEA's migration entry point on the EVM side. The Cosmos layer no longer has a dedicated migration message — the UEA contract is the source of truth for who is allowed to migrate it and to what implementation.
```
