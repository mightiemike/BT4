## Finding: Uninitialized upgradeable system contracts at genesis allow front-running of `initialize()` (analog of un-called `__initializeGrantor`)

The external report describes a contract whose access-control `__initializeGrantor` initializer is never invoked by `init()`. Push Chain has a structurally identical pattern in its genesis-time EVM bootstrap: the node deploys upgradeable proxy contracts (bytecode + storage slots only) but never calls their Solidity `initialize(...)` function, leaving a window where any unprivileged caller can call it first and seize ownership/roles.

### Title
Genesis-deployed upgradeable system contracts (Factory, UniversalCore/UniversalGatewayPC) are never initialized on-chain, allowing an unprivileged attacker to front-run `initialize()` and seize `MANAGER_ROLE`/ownership — (File: `x/uregistry/keeper/genesis.go`, `x/uexecutor/keeper/genesis.go`)

### Summary
`x/uregistry`'s `deploySystemContracts` and `x/uexecutor`'s `deployFactoryEA` install proxy, proxy-admin, and implementation bytecode plus raw EIP-1967 storage slots directly via `evmKeeper.SetAccount`/`SetCode`/`SetState` at genesis [1](#0-0) . Neither this code path nor `x/uexecutor`'s equivalent [2](#0-1)  ever calls the contracts' `initialize(...)` function. The only place `initialize` is invoked is in test helpers (`setupFactoryContract`) and in operator-run e2e setup scripts (`forge script script/localSetup/setup.s.sol`) [3](#0-2) [4](#0-3) , run manually, after node start.

### Finding Description
The Factory contract's `initialize(address initialOwner)` and UniversalCore's `initialize(address wpc_, address uniswapV3Factory_, address uniswapV3SwapRouter_, address uniswapV3Quoter_)` are OpenZeppelin-style initializer functions guarded only by the standard `initializer` modifier, which prevents *re*-initialization but does not restrict *who* can call it first [5](#0-4) [6](#0-5) .

At genesis, `InitGenesis` for `x/uregistry` deploys `UNIVERSAL_GATEWAY_PC` and the reserved proxy slots by directly writing bytecode and EIP-1967 slots — no call to `initialize` is made [7](#0-6) . Likewise `x/uexecutor`'s `InitGenesis` deploys the UEA Factory proxy/impl/admin the same way [8](#0-7) , and the module's own README confirms: "On fresh genesis... it deploys the UEA factory contract" with no mention of initialization [9](#0-8) .

Since these are the same addresses on every fresh chain (deterministic addresses like `0x...C1`), and the EVM JSON-RPC endpoint accepts transactions from genesis height onward, any unprivileged external account can submit a plain `initialize(...)` call to these addresses before the operator's own setup script does. Because `initialize` sets `owner` (Factory) or grants foundational admin/roles used later by `grantRole(MANAGER_ROLE, ...)` (UniversalCore) [10](#0-9) , whoever wins that race becomes the privileged owner/admin of the contract that controls gas-token PRC20 mapping, base gas limits, L1 gas fees, and TSS fund-migration gas limits [11](#0-10) .

### Impact Explanation
An attacker who front-runs `initialize()` on the Factory or UniversalCore contract could:
- Set themselves as Factory `owner`, then call `setUEAProxyImplementation` to point every future UEA proxy at malicious implementation bytecode — enabling arbitrary code execution in the context of every user's Universal Executor Account, i.e., unauthorized UEA execution and theft of all deposited funds.
- Become UniversalCore's privileged role holder and grant themselves `MANAGER_ROLE`, then call `updateGasTokenPRC20`, `setL1GasFeeByChain`, `updateBaseGasLimitByChain`, corrupting gas/PRC20 token accounting and routing, or mis-mapping gas tokens to attacker-controlled PRC20 contracts (unauthorized mint path).

This falls squarely within the "Registry and accounting path" and "unauthorized module-originated EVM execution" impact categories in scope.

### Likelihood Explanation
Exploitability is time-window-dependent: it requires the attacker to submit the `initialize` transaction after the chain's first block (when the RPC becomes live and the bytecode exists) but before the deployer/operator's own setup script executes its `initialize` call. On any public devnet/testnet/mainnet launch, or any re-genesis event, this window is real and externally observable (the proxy addresses and required calldata are public/deterministic), making it plausible for an attacker with fast automation (mempool watcher) to win the race, especially given block times and the operator script's own multi-step sequential nature (funding, wiring, calling initialize, granting roles) visible in `e2e-tests/setup.sh`.

### Recommendation
Call the Solidity `initialize(...)` entrypoint for every deployed upgradeable system contract synchronously within the same `InitGenesis` keeper logic that deploys the bytecode (via `evmKeeper.CallEVM`/`DerivedEVMCall` before `LoadLatestVersion` completes), so the contract is fully initialized atomically at genesis with no externally reachable uninitialized window. Alternatively, embed the initializing storage slots directly during `SetState` (as is already done for the ProxyAdmin's `owner` slot) rather than relying on a runtime `initialize()` call that must be raced by an operator script.

### Proof of Concept
1. Start a fresh Push Chain node from genesis (mainnet/testnet launch or any node reset that redeploys system contracts, i.e. `Exported=false`).
2. Immediately after the first block (or first RPC-serving block), before the operator's `setup.s.sol` / init script runs, submit a plain EVM transaction to the Factory proxy address calling `initialize(attackerAddress)`.
3. Verify `owner()` on the Factory now returns `attackerAddress` instead of the intended deployer.
4. Call `setUEAProxyImplementation(maliciousImpl)` as the new owner, redirecting all subsequently deployed UEAs to attacker-controlled logic.

### Citations

**File:** x/uregistry/keeper/genesis.go (L97-134)
```go
func deploySystemContracts(ctx context.Context, evmKeeper types.EVMKeeper, systemContracts map[string]types.ContractAddresses) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	// Sort contract names for deterministic iteration order across all validators
	names := make([]string, 0, len(systemContracts))
	for name := range systemContracts {
		names = append(names, name)
	}
	sort.Strings(names)

	for _, name := range names {
		contract := systemContracts[name]

		proxyAddr := common.HexToAddress(contract.Address)
		if isContractDeployed(sdkCtx, evmKeeper, proxyAddr) {
			sdkCtx.Logger().Info(
				"system contract already deployed, skipping",
				"name", name,
				"proxy", proxyAddr.Hex(),
			)
			continue
		}

		bytecodes, ok := types.BYTECODE[name]
		if !ok {
			panic(fmt.Sprintf("no bytecode found for contract %s", name))
		}

		// 1. Deploy ProxyAdmin with ADMIN_RUNTIME
		deployProxyAdminContract(ctx, evmKeeper, contract.ProxyAdmin, bytecodes.ADMIN_RUNTIME)

		// 2. Deploy Implementation with IMPL_RUNTIME
		deployImplementationContract(ctx, evmKeeper, contract.Implementation, bytecodes.IMPL_RUNTIME)

		// 3. Deploy Proxy with PROXY_RUNTIME
		deployProxyContract(ctx, evmKeeper, contract.Address, contract.ProxyAdmin, contract.Implementation, bytecodes.PROXY_RUNTIME)
	}
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

**File:** test/utils/contracts_setup.go (L118-125)
```go
	// Check initial factory owner
	ownerResult, err := app.EVMKeeper.CallEVM(ctx, factoryABI, owner, factoryAddr, true, nil, "owner")
	require.NoError(t, err)
	t.Logf("Factory owner after genesis: %s", common.BytesToAddress(ownerResult.Ret).Hex())

	// Initialize factory with owner
	_, err = app.EVMKeeper.CallEVM(ctx, factoryABI, owner, factoryAddr, true, nil, "initialize", owner)
	require.NoError(t, err)
```

**File:** e2e-tests/setup.sh (L4141-4143)
```shellscript
    log_err "Re-run the gateway setup: forge script script/localSetup/setup.s.sol --broadcast --rpc-url \$PUSH_RPC_URL --private-key \$PRIVATE_KEY"
    exit 1
  fi
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

**File:** x/uexecutor/types/abi.go (L269-292)
```go
    {
      "type": "function",
      "name": "initialize",
      "inputs": [
        { "name": "wpc_", "type": "address", "internalType": "address" },
        {
          "name": "uniswapV3Factory_",
          "type": "address",
          "internalType": "address"
        },
        {
          "name": "uniswapV3SwapRouter_",
          "type": "address",
          "internalType": "address"
        },
        {
          "name": "uniswapV3Quoter_",
          "type": "address",
          "internalType": "address"
        }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
    },
```

**File:** x/uexecutor/types/abi.go (L333-341)
```go
      "type": "function",
      "name": "grantRole",
      "inputs": [
        { "name": "role",    "type": "bytes32", "internalType": "bytes32" },
        { "name": "account", "type": "address", "internalType": "address" }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
    },
```

**File:** x/uregistry/README.md (L65-77)
```markdown
## EVM Integration

On fresh genesis (`Exported=false`), `InitGenesis` calls `deploySystemContracts` to install:

| Slot | Address |
|---|---|
| `UNIVERSAL_GATEWAY_PC` | `0x00000000000000000000000000000000000000C1` (proxy) |
| `RESERVED_0` | `0x00000000000000000000000000000000000000B0` |
| `RESERVED_1` | `0x00000000000000000000000000000000000000B1` |
| `RESERVED_2` | `0x00000000000000000000000000000000000000B2` |
| `UNIVERSAL_BATCH_CALL` | `0x00000000000000000000000000000000000000Bc` |

These are EIP-1967 transparent proxies — runtime-deployed bytecode is committed verbatim in `keeper.go`. Helper functions `ReserveUGPC` and `FixReservedBytecode` exist for in-place upgrade migrations to (re)install bytecode without redeploying through normal EVM calls.
```

**File:** x/uexecutor/keeper/keeper.go (L166-177)
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
```

**File:** x/uexecutor/README.md (L304-308)
```markdown
## EVM Integration

`x/uexecutor` is unusual in that it issues EVM calls as a Cosmos module. On fresh genesis (`Exported=false`) it deploys the **UEA factory** contract. Thereafter, every inbound execution, refund, swap quote, and chain-meta update flows through `DerivedEVMCall` with the manually tracked `ModuleAccountNonce` so successive calls in the same block don't collide.

Re-deploying the factory on genesis import is explicitly skipped — see `keeper.go:155-159` — because that would overwrite live EVM state and shift the deterministic addresses of every UEA on chain.
```

**File:** test/integration/utss/fund_migration_test.go (L30-61)
```go
const universalCoreSetupABI = `[
    {
      "type": "function",
      "name": "grantRole",
      "inputs": [
        { "name": "role",    "type": "bytes32", "internalType": "bytes32" },
        { "name": "account", "type": "address", "internalType": "address" }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
    },
    {
      "type": "function",
      "name": "setL1GasFeeByChain",
      "inputs": [
        { "name": "chainNamespace", "type": "string",  "internalType": "string" },
        { "name": "l1GasFee",       "type": "uint256", "internalType": "uint256" }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
    },
    {
      "type": "function",
      "name": "setTssFundMigrationGasLimitByChain",
      "inputs": [
        { "name": "chainNamespace", "type": "string",  "internalType": "string" },
        { "name": "gasLimit",       "type": "uint256", "internalType": "uint256" }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
    }
]`
```
