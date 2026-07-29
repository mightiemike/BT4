## Summary

The reported "uninitialized upgradeable implementation" bug class has a direct, high-impact analog in Push Chain's genesis-time deployment of the **UEA Factory proxy** in `x/uexecutor`. The proxy/implementation pair is materialized by directly writing EVM account/storage state (bypassing constructors entirely), and the `initialize(address)` call that is supposed to set the `Ownable` owner is **not** invoked as part of that atomic genesis step — it is left as a separate, later call. Because the storage is written directly (no constructor ever runs, so no `_disableInitializers()`-equivalent is possible), the Factory proxy sits in an "owner not yet set" state that is reachable and callable by any unprivileged account until the legitimate `initialize(owner)` transaction lands.

### Finding Description

Genesis deployment of the Factory proxy/impl (`x/uexecutor/keeper/genesis.go`) writes account code and EIP-1967 storage slots directly via `evmKeeper.SetAccount` / `SetCode` / `SetState`: [1](#0-0) 

This never executes a constructor and never calls `initialize()` — contrast with `deployProxyAdminContract`, which is the *only* deployer that proactively seeds an owner-equivalent storage slot directly: [2](#0-1) 

The Factory's ABI exposes an `initialize(address initialOwner)` entry point (OZ-style `Ownable` initializer, selector `0xc4d66de8` present in `FactoryImplRuntimeBytecode`): [3](#0-2) [4](#0-3) 

The integration test harness confirms `initialize()` is a **separate, later, ordinary EVM call** distinct from genesis deployment — i.e., production genesis does not atomically call it: [5](#0-4) 

A `grep` across `x/uexecutor/keeper/genesis.go` shows no `initialize` call co-located with `deployFactoryProxy`/`deployFactoryImplContract`, confirming the same gap the external report describes (implementation/proxy left initializable by any caller) exists here as a genesis-timing gap rather than a missing-constructor-guard, but with the same root cause: **no enforced, atomic ownership binding at deployment time**.

### Impact Explanation

The Factory at `0x00...00ea` is the canonical UEA factory used throughout universal execution — `DeployUEA`, `registerNewChain`, `registerUEA`, and critically `setUEAProxyImplementation` are all owner-gated calls on this exact contract: [6](#0-5) 

If an unprivileged attacker manages to call `initialize(attacker)` before the legitimate deployer's `initialize(admin)` transaction is processed, the attacker becomes `owner` of the Factory. As owner they can call `setUEAProxyImplementation(maliciousImpl)`, silently redirecting the implementation used by every UEA subsequently deployed via `DeployUEA` / `DeployUEAV2` (the path every inbound execution and `ExecutePayload` auto-deploy relies on): [7](#0-6) 

Since all cross-chain inbound funds/payloads route through UEA deployment and execution, a hijacked implementation pointer would let the attacker drain or misdirect user/protocol funds credited into subsequently-deployed UEAs — squarely within the "unauthorized UEA execution" / "draining of user or protocol-controlled funds" allowed-impact categories.

### Likelihood Explanation

This is **not** exploitable once the chain has been running normally and the legitimate `initialize(admin)` call has already landed (the standard `Ownable` "already initialized" revert then applies to any second caller). The exposure window is narrow and specific: the interval between genesis state being committed (Factory code/storage present, owner unset) and the first legitimate `initialize()` transaction being included in a block. On a public chain where the mempool is open before/at genesis or during network bootstrap/migration to new deployments of this same pattern (e.g. any future redeploy of `FACTORY_IMPL_ADDRESS_HEX`/`FACTORY_PROXY_ADDRESS_HEX` at a new address, or a reset network), any unprivileged actor able to submit a transaction in that window can win the race. I could not verify from the indexed code whether there is a privileged, same-block, guaranteed-ordering mechanism (e.g., an ante-handler restriction, or a genesis-embedded EVM call) that forecloses this window in the current deployment procedure — this is the main uncertainty in the finding, and it should be confirmed against the actual mainnet/testnet genesis/bootstrap runbook (not just the Go code paths indexed here).

### Recommendation

Bind the Factory owner atomically within the same genesis state-transition that deploys its code/storage — the same way `deployProxyAdminContract` already does for `ProxyAdmin.owner` via a direct `SetState` write — rather than relying on a subsequent, separately-submitted `initialize()` transaction. Concretely: extend `deployFactoryImplContract`/`deployFactoryProxy` (or their v2/registry equivalents) to also directly `SetState` the owner storage slot to the intended admin at genesis, or alternatively fold the `initialize(admin)` call into the same genesis handler that deploys the bytecode so no genesis block/state can ever exist with Factory ownership unset. This removes the window entirely rather than relying on transaction-ordering luck.

### Proof of Concept

1. At genesis (or at any future re-deployment of a Factory-style proxy following this same pattern), `deployFactoryProxy`/`deployFactoryImplContract` install code and EIP-1967 storage slots for the Factory but do **not** set an owner.
2. Before the legitimate admin's `initialize(adminAddr)` transaction is included in a block, an unprivileged attacker submits `initialize(attackerAddr)` targeting the Factory proxy address (`0x00...00ea`).
3. Standard `Ownable`/`Initializable` semantics accept the first `initialize()` call; the attacker becomes `owner`.
4. Attacker calls `setUEAProxyImplementation(maliciousImpl)` on the now attacker-owned Factory.
5. Every subsequent `DeployUEA`/`DeployUEAV2` call (triggered by ordinary user inbound deposits/payloads) deploys a UEA proxy pointing at the attacker's malicious implementation, giving the attacker control over execution/funds for those UEAs.

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

**File:** x/uexecutor/types/constants.go (L25-25)
```go
var FactoryImplRuntimeBytecode = common.FromHex("60806040526004361015610011575f80fd5b5f803560e01c8063031bc85b146116ac5780630772e63c146116245780630d7c4b37146115bb5780632ab6b9c21461153257806330363dd2146114d557806330b4a521146114845780634716fb2d1461144857806357b1f59b1461121b578063715018a61461114157806372b3f38b146110125780638da5cb5b14610fa25780639538c4b314610f5e578063a10bbfb914610f28578063b46eee2114610ea0578063b4cb9f8c14610da4578063c4d66de814610b83578063cc005a4814610455578063d0f4b0971461027d578063e720582e1461021f578063edb6a18a146101d7578063f2fde38b1461018c578063f861134e1461016b5763f8ba7e0314610117575f80fd5b3461016857807ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc36011261016857602073ffffffffffffffffffffffffffffffffffffffff60055416604051908152f35b80fd5b50346101685760206101846 ... (truncated)
```

**File:** test/utils/contracts_setup.go (L107-125)
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
```

**File:** x/uexecutor/keeper/msg_deploy_uea.go (L1-34)
```go
package keeper

import (
	"context"

	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/ethereum/go-ethereum/common"
	"github.com/pushchain/push-chain-node/x/uexecutor/types"
)

// updateParams is for updating params collections of the module
func (k Keeper) DeployUEA(ctx context.Context, evmFrom common.Address, universalAccountId *types.UniversalAccountId) ([]byte, error) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Info("deploy UEA via msg",
		"chain_namespace", universalAccountId.ChainNamespace,
		"chain_id", universalAccountId.ChainId,
		"owner", universalAccountId.Owner,
		"from", evmFrom.Hex(),
	)

	// EVM Call arguments
	factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

	// Use your keeper CallEVM directly
	receipt, err := k.CallFactoryToDeployUEA(
		sdkCtx,
		evmFrom,
		factoryAddress,
		universalAccountId,
	)
	if err != nil {
		return nil, err
	}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L48-78)
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

		k.Logger().Info("auto-deploying UEA before execute (pre-funded address)",
			"uea", ueaAddr.Hex(),
			"balance", balance.Amount.String(),
			"chain", caip2Identifier,
			"owner", universalAccountId.Owner,
		)
		if _, err := k.DeployUEAV2(ctx, evmFrom, universalAccountId); err != nil {
			return errors.Wrapf(err, "failed to auto-deploy pre-funded UEA")
		}
	}
```
