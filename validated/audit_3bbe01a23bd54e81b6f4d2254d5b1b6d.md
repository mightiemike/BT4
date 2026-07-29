### Title
Unprotected `initialize(address)` on the UEA Factory proxy allows first-caller takeover after genesis deployment — ([File: x/uexecutor/keeper/genesis.go])

### Summary
The `uexecutor` module deploys the Universal Executor Account (UEA) Factory as an EIP-1967 transparent proxy directly at genesis via raw EVM state writes (`SetAccount`/`SetCode`), but never calls the Factory's `initialize(address initialOwner)` method as part of `InitGenesis`. This leaves the Factory's `Ownable` owner storage slot at the zero value immediately after genesis. Because `initialize` is a plain, permissionless function until it has been called once, whichever address's transaction reaches `initialize()` first becomes the permanent owner of the Factory — a role that controls `setUEAProxyImplementation`, `registerNewChain`, and `registerUEA`.

### Finding Description
`deployFactoryEA` in [1](#0-0)  performs three raw deployments — implementation, ProxyAdmin, and the Factory proxy — using `evmKeeper.SetAccount`/`SetCode`/`SetState` directly, bypassing normal constructor/initializer execution: [2](#0-1) [3](#0-2) 

Notice the contrast with `deployProxyAdminContract` in the very same file (and the analogous function in `uregistry`), which explicitly hardcodes the `Ownable.owner` slot (`common.Hash{}`) to `PROXY_ADMIN_OWNER_ADDRESS_HEX` at genesis time, closing any initialization race for that contract: [4](#0-3) 

No equivalent direct write exists for the Factory's own `owner` storage slot. Instead, the Factory ABI exposes a standard OpenZeppelin-style `initialize(address initialOwner)` entry point: [5](#0-4) 

The test harness confirms this is meant to be invoked as a *separate* EVM call after genesis-time deployment, exactly mirroring the pattern the original report describes (implementation/proxy deployed, then a distinct transaction claims ownership): [6](#0-5) 

`InitGenesis` for `uexecutor` only gates the deployment step on `!data.Exported`; it never issues the `initialize` call itself: [7](#0-6) 

I was not able to locate the production code path (chain start scripts / upgrade handler) that performs the post-genesis `initialize(owner)` call for a live network in this repository index — only the Go test helper exercises it. If, in the real deployment procedure, this call is submitted as an ordinary transaction after `InitChain`/genesis rather than being folded into `InitGenesis` (as the test helper's two-step pattern suggests), any unprivileged party observing the mempool at chain start (or simply racing the very first blocks) can submit their own `initialize(attacker)` call first.

### Impact Explanation
Whoever wins the race to call `initialize` becomes the sole `owner` of the Factory contract, which gates:
- `setUEAProxyImplementation` — the bytecode template used for every future UEA;
- `registerNewChain` / `registerUEA` — which VM implementation address is used to deploy UEAs for each chain/VM pair.

Controlling these lets an attacker point all subsequently deployed UEAs at a malicious implementation, allowing arbitrary unauthorized `UEA`/`CEA` execution and theft of any funds routed through Universal Executor Accounts — squarely within the "unauthorized UEA or CEA execution" and "stealing/draining ... user or protocol-controlled funds" impact categories.

### Likelihood Explanation
This is uncertain without visibility into the exact operational sequence used to bring a real network online. If the operator's `initialize` call is bundled atomically with genesis (e.g., executed inside `InitChain` before the chain accepts any external transaction), there is no attacker-reachable window and the issue does not apply. I could not find such atomic wiring in the indexed code — `InitGenesis` for `uexecutor` deploys the proxy but does not call `initialize` — which is the concerning asymmetry relative to the `ProxyAdmin` contracts that deliberately avoid this exact class of bug via direct slot-writes.

### Recommendation
Set the Factory `owner` storage slot directly during `InitGenesis` (the same pattern already used for `ProxyAdmin.owner` in `deployProxyAdminContract`), instead of relying on a subsequent, unprotected `initialize` transaction. Alternatively, if `initialize` must remain callable, ensure it is invoked as part of the same atomic `InitGenesis`/`InitChain` execution so no externally-submittable transaction can precede it.

### Proof of Concept
Not executable from the available index — this requires confirming, against the actual mainnet/testnet bootstrap procedure (outside what's indexed here), whether `factory.initialize(owner)` is submitted as a standalone transaction after chain start. I recommend a Devin session with full repository/infra access to trace the exact genesis bootstrap sequence (`local-native/scripts/setup-genesis-auto.sh`, `e2e-tests/setup.sh`, and any mainnet/testnet launch runbooks) to confirm or rule out the race window before treating this as validated.

### Citations

**File:** x/uexecutor/keeper/genesis.go (L16-46)
```go
func deployFactoryProxy(ctx context.Context, evmKeeper types.EVMKeeper) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	proxyAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)
	proxyAdminOwner := common.HexToAddress(types.PROXY_ADMIN_ADDRESS_HEX)
	factoryImplAddress := common.HexToAddress(types.FACTORY_IMPL_ADDRESS_HEX)

	// Compute the code hash from the runtime bytecode
	codeHash := crypto.Keccak256(types.ProxyRuntimeBytecode)

	// Create the EVM account object
	evmAccount := statedb.Account{
		Nonce:    1,             // to prevent tx nonce=0 conflicts
		Balance:  new(uint256.Int), // zero balance by default
		CodeHash: codeHash,      // link to deployed code
	}

	// Set the EVM account with the factory proxy contract
	err := evmKeeper.SetAccount(sdkCtx, proxyAddress, evmAccount)
	if err != nil {
		panic("failed to set factory proxy contract account: " + err.Error())
	}

	// Store the runtime bytecode linked to the code hash
	evmKeeper.SetCode(sdkCtx, codeHash, types.ProxyRuntimeBytecode)

	// Update proxyAdmin Slot with the proxyAdmin owner address (left padded to 32 bytes)
	evmKeeper.SetState(sdkCtx, proxyAddress, types.PROXY_ADMIN_SLOT, common.LeftPadBytes(proxyAdminOwner.Bytes(), 32))

	// Update proxyImplementation Slot with the factory implementation address (left padded to 32 bytes)
	evmKeeper.SetState(sdkCtx, proxyAddress, types.PROXY_IMPLEMENTATION_SLOT, common.LeftPadBytes(factoryImplAddress.Bytes(), 32))
}
```

**File:** x/uexecutor/keeper/genesis.go (L48-70)
```go
func deployFactoryImplContract(ctx context.Context, evmKeeper types.EVMKeeper) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	factoryAddress := common.HexToAddress(types.FACTORY_IMPL_ADDRESS_HEX)

	// Compute the code hash from the runtime bytecode
	codeHash := crypto.Keccak256(types.FactoryImplRuntimeBytecode)

	// Create the EVM account object
	evmAccount := statedb.Account{
		Nonce:    1,             // to prevent tx nonce=0 conflicts
		Balance:  new(uint256.Int), // zero balance by default
		CodeHash: codeHash,      // link to deployed code
	}

	// Set the EVM account with the factory contract
	err := evmKeeper.SetAccount(sdkCtx, factoryAddress, evmAccount)
	if err != nil {
		panic("failed to set factory contract account: " + err.Error())
	}

	// Store the runtime bytecode linked to the code hash
	evmKeeper.SetCode(sdkCtx, codeHash, types.FactoryImplRuntimeBytecode)
}
```

**File:** x/uexecutor/keeper/genesis.go (L72-98)
```go
func deployProxyAdminContract(ctx context.Context, evmKeeper types.EVMKeeper) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	proxyAdminAddress := common.HexToAddress(types.PROXY_ADMIN_ADDRESS_HEX)
	owner := common.HexToAddress(types.PROXY_ADMIN_OWNER_ADDRESS_HEX)

	// Compute the code hash from the runtime bytecode
	codeHash := crypto.Keccak256(types.ProxyAdminRuntimeBytecode)

	// Create the EVM account object
	evmAccount := statedb.Account{
		Nonce:    1,             // to prevent tx nonce=0 conflicts
		Balance:  new(uint256.Int), // zero balance by default
		CodeHash: codeHash,      // link to deployed code
	}

	// Set the EVM account with the proxy admin contract
	err := evmKeeper.SetAccount(sdkCtx, proxyAdminAddress, evmAccount)
	if err != nil {
		panic("failed to set proxy admin contract account: " + err.Error())
	}

	// Store the runtime bytecode linked to the code hash
	evmKeeper.SetCode(sdkCtx, codeHash, types.ProxyAdminRuntimeBytecode)

	// Initialize storage slot 0 (Ownable.owner) with the owner address (left padded to 32 bytes)
	evmKeeper.SetState(sdkCtx, proxyAdminAddress, common.Hash{}, common.LeftPadBytes(owner.Bytes(), 32))
}
```

**File:** x/uexecutor/keeper/genesis.go (L100-109)
```go
func deployFactoryEA(ctx context.Context, evmKeeper types.EVMKeeper) {
	// Deploy the factory implementation contract
	deployFactoryImplContract(ctx, evmKeeper)

	// Deploy the proxy admin contract
	deployProxyAdminContract(ctx, evmKeeper)

	// Deploy the factory proxy contract
	deployFactoryProxy(ctx, evmKeeper)
}
```

**File:** x/uexecutor/types/abi.go (L14-24)
```go
// FactoryV1ABI contains the ABI for the factory contract
const FactoryV1ABI = `[
  {
    "type": "function",
    "name": "initialize",
    "inputs": [
      { "name": "initialOwner", "type": "address", "internalType": "address" }
    ],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
```

**File:** test/utils/contracts_setup.go (L107-130)
```go
func setupFactoryContract(
	t *testing.T,
	app *app.ChainApp,
	ctx sdk.Context,
	factoryABI abi.ABI,
	opts AppSetupOptions,
	accounts TestAccounts,
) error {
	factoryAddr := opts.Addresses.FactoryAddr
	owner := common.BytesToAddress(accounts.DefaultAccount.GetAddress().Bytes())

	// Check initial factory owner
	ownerResult, err := app.EVMKeeper.CallEVM(ctx, factoryABI, owner, factoryAddr, true, nil, "owner")
	require.NoError(t, err)
	t.Logf("Factory owner after genesis: %s", common.BytesToAddress(ownerResult.Ret).Hex())

	// Initialize factory with owner
	_, err = app.EVMKeeper.CallEVM(ctx, factoryABI, owner, factoryAddr, true, nil, "initialize", owner)
	require.NoError(t, err)

	// Verify owner is set
	ownerResult, err = app.EVMKeeper.CallEVM(ctx, factoryABI, owner, factoryAddr, true, nil, "owner")
	require.NoError(t, err)
	t.Logf("Factory owner after initialization: %s", common.BytesToAddress(ownerResult.Ret).Hex())
```

**File:** x/uexecutor/keeper/keeper.go (L166-181)
```go
// InitGenesis initializes the module's state from a genesis state.
func (k *Keeper) InitGenesis(ctx context.Context, data *types.GenesisState) error {

	if err := data.Params.ValidateBasic(); err != nil {
		return err
	}

	// Only deploy factory contracts on fresh genesis, not on import from export.
	// Re-deploying on import would overwrite existing EVM state or cause nonce collisions.
	if !data.Exported {
		deployFactoryEA(ctx, k.evmKeeper)
	}

	if err := k.Params.Set(ctx, data.Params); err != nil {
		return err
	}
```
