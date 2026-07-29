## Finding: Factory Contract Deployed at Genesis Without Atomic Initialization — Front-Runnable Ownership Takeover

### Title
Unprivileged attacker can seize ownership of the UEA Factory contract via un-initialized `initialize()` after genesis deployment - (File: `x/uexecutor/keeper/genesis.go`)

### Summary
`x/uexecutor`'s `InitGenesis` deploys the UEA Factory (`FactoryV1`) proxy and implementation directly into EVM state by manually writing bytecode and storage slots, but never calls the Factory's `initialize(address initialOwner)` function as part of that same atomic genesis step. This is the exact bug class from the referenced report: contract code becomes live and callable before its initializer sets a legitimate owner, creating a window in which any unprivileged actor can call `initialize` first and become the Factory's owner.

### Finding Description
`deployFactoryEA` (called from `InitGenesis` when `!data.Exported`) performs three raw state-injection steps — `deployFactoryImplContract`, `deployProxyAdminContract`, `deployFactoryProxy` — using `evmKeeper.SetAccount`/`SetCode`/`SetState` to place bytecode and hand-set the ProxyAdmin/implementation storage slots directly: [1](#0-0) 

None of these steps calls `initialize` on the Factory implementation. The Factory ABI exposes `initialize(address initialOwner)` as a plain `nonpayable` function with no caller restriction encoded in the ABI itself: [2](#0-1) 

The only place in the entire repository that actually invokes `initialize` on the Factory is test setup code, which calls it as a separate, subsequent `CallEVM` after genesis has already made the contract live and callable: [3](#0-2) 

This mirrors the test utility's own logging comment ("Factory owner after genesis" vs. "Factory owner after initialization"), which shows the owner is unset (zero-value) immediately following genesis and only becomes correct after a distinct, later `initialize` call. In production there is no equivalent code path in `x/uexecutor/keeper/genesis.go` (or elsewhere in `x/`) that performs this second call atomically with genesis. If a live network relies on a post-genesis admin transaction to call `initialize(adminAddr)` on the Factory (analogous to what the test harness does manually), any unprivileged party who observes the mempool or otherwise transacts before that admin transaction lands can submit their own `initialize(attackerAddr)` call to the Factory (address `0x00000000000000000000000000000000000000fa` / proxy `...ea`) first, since Solidity's `initializer` modifier from OpenZeppelin only prevents a *second* call — it does not restrict *who* can make the first call.

### Impact Explanation
If an attacker wins this race and becomes the Factory `owner`, they gain access to owner-privileged Factory functions such as `setUEAProxyImplementation`, `registerNewChain`, and `registerUEA`. Because `x/uexecutor` computes and deploys every UEA (Universal Externally-owned Account) through this same Factory (`CallFactoryToDeployUEA`, `CallFactoryToGetUEAAddressForOrigin`, `DeployUEAV2`), an attacker-controlled Factory owner could repoint `UEA_PROXY_IMPLEMENTATION` to a malicious implementation. Every UEA subsequently deployed through the Factory delegates to that implementation, giving the attacker unauthorized control over UEA execution logic — a direct violation of the "unauthorized UEA execution" and "unauthorized module-originated EVM execution" invariants in scope, with potential to drain or freeze user funds routed through UEAs.

### Likelihood Explanation
This requires: (1) production deployment relies on a non-atomic, separate `initialize` transaction after genesis (as the test-only pattern demonstrates), and (2) the attacker races that transaction. This is uncertain from static analysis alone — I could not locate any production code path (only test scaffolding) that performs the post-genesis `initialize` call, so I cannot confirm whether mainnet/testnet genesis actually leaves this window open or whether it is closed by an out-of-repo deployment procedure. This uncertainty should be resolved by inspecting the actual chain bootstrap/deployment runbook and whether `initialize` is called in the same block/transaction as code deployment, or gated by some other on-chain check not visible in the embedded bytecode (which I could not decompile within available tools).

### Recommendation
- Call `initialize(admin)` on the Factory implementation atomically inside `deployFactoryEA` during `InitGenesis`, in the same state-transition as the bytecode/storage injection, rather than via a separate later transaction.
- Alternatively, have the Factory implementation determine its owner deterministically from genesis-injected storage (as is already done for `PROXY_ADMIN_SLOT`/`PROXY_IMPLEMENTATION_SLOT`) rather than requiring a callable `initialize` function post-deployment.
- If `initialize` must remain callable, verify the actual Solidity source restricts it (e.g., via the `initializer` modifier plus a hard-coded/genesis-set expected caller) — the on-chain access control could not be verified from the embedded bytecode alone, since no Solidity source is checked into this repo.

### Proof of Concept
1. Observe the network at genesis/first-block; the Factory (`0x...ea` proxy / `0x...fa` impl) has bytecode and delegatecall wiring set but no owner (per test-util's own "owner after genesis" log showing zero/unset).
2. Before the legitimate admin's `initialize(admin)` transaction is included, attacker submits `initialize(attackerAddr)` targeting the Factory proxy.
3. Attacker's transaction is included first (no special privilege required, `initializer` modifier only blocks repeat calls, not the first caller).
4. Attacker now owns the Factory and can call `setUEAProxyImplementation` to redirect all future UEA deployments to attacker-controlled logic.

I was unable to fully verify whether Push Chain's actual production genesis/bootstrap process performs the `initialize` call atomically outside of this repository's Go code (e.g., via an out-of-band script). This should be confirmed before treating this as fully exploitable in production; the code in this repo alone (`x/uexecutor/keeper/genesis.go`) does not perform that call.

### Citations

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

**File:** test/utils/contracts_setup.go (L118-130)
```go
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
