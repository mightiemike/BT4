### Title
Factory proxy `initialize(address)` is never called at genesis, allowing an unprivileged attacker to claim ownership and hijack the UEA implementation - (File: x/uexecutor/keeper/genesis.go)

### Summary
`x/uexecutor`'s genesis routine deploys the UEA Factory proxy, its implementation, and its ProxyAdmin directly into EVM state via raw `SetAccount`/`SetCode`/`SetState` calls, but never invokes the Factory's `initialize(address initialOwner)` method. This is the same bug class as "Ownable init is not called": the contract that gates `setUEAProxyImplementation` (and other administrative functions) is deployed with no owner set, so whichever unprivileged account calls `initialize` first — including an attacker — becomes the permanent owner of the Factory.

### Finding Description
`deployFactoryEA` in [1](#0-0)  deploys three pieces of bytecode at genesis (`deployFactoryImplContract`, `deployProxyAdminContract`, `deployFactoryProxy`) but performs no EVM call to `initialize`. It is invoked from `InitGenesis` unconditionally on fresh chains: [2](#0-1) .

The Factory's ABI explicitly exposes an `initialize(address initialOwner)` method, matching the OpenZeppelin `Initializable` pattern (selector `0xc4d66de8` present in `FactoryImplRuntimeBytecode`): [3](#0-2)  and [4](#0-3) .

The only place in the entire repository where `initialize` is actually called on the Factory is test-only setup code — never production genesis, never a message handler, never an `app/upgrades/*` migration (a search of `app/**/*.go` for `FACTORY_PROXY_ADDRESS_HEX`/`initialize` found no production call site): [5](#0-4) .

Production code paths that interact with the Factory only ever call `deployUEA` (permissionless, no ownership check needed) — never `initialize`: [6](#0-5)  and [7](#0-6) .

Since `initialize` is a one-time initializer, on a real network the Factory at the deterministic reserved address `0x00000000000000000000000000000000000000eA` sits owner-less from genesis. Any unprivileged external account can submit a normal EVM transaction calling `initialize(attackerAddress)` and become the sole owner — there is no legitimate transaction racing to claim it first.

### Impact Explanation
Once an attacker owns the Factory, they control `setUEAProxyImplementation`, which sets the implementation address used by every UEA proxy deployed through `deployUEA` from that point forward (and any code path using `registerNewChain`/`registerUEA`, also gated by the same owner). Because every source-chain user's UEA is deployed through this Factory as a proxy pointing at that implementation, an attacker-controlled implementation can arbitrarily drain, redirect, or corrupt execution for every UEA subsequently deployed — a direct path to unauthorized fund draining and unauthorized UEA execution, which is explicitly in scope ("unauthorized UEA or CEA execution", "stealing/draining ... of user or protocol-controlled funds").

### Likelihood Explanation
High if no other initialization path exists: the attack requires only a single unprivileged EVM transaction to the well-known reserved Factory address, and no competing legitimate call exists anywhere in the reachable codebase to claim ownership first. Note: I was unable to fully audit every `app/upgrades/*` migration handler (there are ~25) within the available tool budget; it is possible one of the audit-fix upgrades (e.g. `ai-audit-fixes`, `ai-audit-fixes-2`, `proxy-bytecode-fix`) already remediates this by calling `initialize` post-genesis. This should be verified directly against those files before treating the finding as unpatched on the live chain.

### Recommendation
Call `Factory.initialize(<trusted owner, e.g. PROXY_ADMIN_OWNER_ADDRESS_HEX or a governance-controlled address>)` from within `deployFactoryEA` (or immediately after, in the same genesis transaction) using the module's `DerivedEVMCall`/`CallEVM`, mirroring what `deployProxyAdminContract` already does for the Ownable `ProxyAdmin` (writing storage slot 0 directly). Alternatively, set the Factory's owner storage slot directly at genesis the same way `PROXY_ADMIN_OWNER_ADDRESS_HEX` is written into the ProxyAdmin's slot 0, rather than relying on an initializer call that no code path ever triggers. Add a genesis-time invariant check/unit test (similar to `TestDeploySystemContracts_DeploysFullTripleForEveryReservedAddress`) asserting the Factory's owner is non-zero and equals the intended trusted address immediately after `InitGenesis`.

### Proof of Concept
1. Launch (or observe) a Push Chain network from genesis; `InitGenesis` runs `deployFactoryEA`, installing the Factory proxy/impl/admin bytecode at `0x00000000000000000000000000000000000000eA` with no owner set.
2. As an unprivileged external account, submit a standard EVM transaction: `Factory.initialize(attackerAddress)` (selector `0xc4d66de8`) to `0x00000000000000000000000000000000000000eA`.
3. Confirm ownership: call `Factory.owner()` — returns `attackerAddress`.
4. Call `Factory.setUEAProxyImplementation(maliciousImplAddress)` as the attacker-owner.
5. Any subsequent inbound execution that triggers `DeployUEAV2`/`CallFactoryToDeployUEA` for a new user now deploys a UEA proxy pointing at the attacker's malicious implementation, giving the attacker control over that user's funds and payload execution.

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

**File:** x/uexecutor/keeper/keeper.go (L173-177)
```go
	// Only deploy factory contracts on fresh genesis, not on import from export.
	// Re-deploying on import would overwrite existing EVM state or cause nonce collisions.
	if !data.Exported {
		deployFactoryEA(ctx, k.evmKeeper)
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

**File:** x/uexecutor/types/constants.go (L12-25)
```go
	FACTORY_PROXY_ADDRESS_HEX     = "0x00000000000000000000000000000000000000eA"
	PROXY_ADMIN_OWNER_ADDRESS_HEX = "0xa96CaA79eb2312DbEb0B8E93c1Ce84C98b67bF11"
	FACTORY_IMPL_ADDRESS_HEX      = "0x00000000000000000000000000000000000000fa"
	PROXY_ADMIN_ADDRESS_HEX       = "0x00000000000000000000000000000000000000AA"
)

var PROXY_ADMIN_SLOT = common.HexToHash("0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103")
var PROXY_IMPLEMENTATION_SLOT = common.HexToHash("0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc")

var ProxyRuntimeBytecode = common.FromHex("0x608060405261000c61000e565b005b7f00000000000000000000000000000000000000000000000000000000000000AA73ffffffffffffffffffffffffffffffffffffffff1633036100d1575f357fffffffff00000000000000000000000000000000000000000000000000000000167f4f1ef28600000000000000000000000000000000000000000000000000000000146100c7576040517fd2b576ec00000000000000000000000000000000000000000000000000000000815260040160405180910390fd5b6100cf6100d9565b565b6100cf610107565b5f806100e8366004818461043e565b8101906100f59190610492565b915091506101038282610117565b5050565b6100cf61011261017e565b6101c2565b610120826101e0565b60405173ffffffffffffffffffffffffffffffffffffffff8316907fbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b905f90a28051156101765761017182826102b3565b505050565b61010 ... (truncated)

var ProxyAdminRuntimeBytecode = common.FromHex("0x608060405260043610610058575f3560e01c80639623609d116100415780639623609d146100aa578063ad3cb1cc146100bd578063f2fde38b14610112575f80fd5b8063715018a61461005c5780638da5cb5b14610072575b5f80fd5b348015610067575f80fd5b50610070610131565b005b34801561007d575f80fd5b505f5460405173ffffffffffffffffffffffffffffffffffffffff90911681526020015b60405180910390f35b6100706100b8366004610351565b610144565b3480156100c8575f80fd5b506101056040518060400160405280600581526020017f352e302e3000000000000000000000000000000000000000000000000000000081525081565b6040516100a191906104c6565b34801561011d575f80fd5b5061007061012c3660046104df565b6101d5565b61013961023d565b6101425f61028f565b565b61014c61023d565b6040517f4f1ef28600000000000000000000000000000000000000000000000000000000815273ffffff ... (truncated)

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

**File:** x/uexecutor/keeper/evm.go (L115-153)
```go
// CallFactoryToDeployUEA deploys a new UEA using factory contract
// Returns deployment response or error if deployment fails
func (k Keeper) CallFactoryToDeployUEA(
	ctx sdk.Context,
	from, factoryAddr common.Address,
	universalAccount *types.UniversalAccountId,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: deployUEA",
		"factory", factoryAddr.Hex(),
		"chain_namespace", universalAccount.ChainNamespace,
		"chain_id", universalAccount.ChainId,
		"owner", universalAccount.Owner,
	)

	abi, err := types.ParseFactoryABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse factory ABI")
	}

	abiUniversalAccount, err := types.NewAbiUniversalAccountId(universalAccount)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal account")
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,        // who is sending the transaction
		factoryAddr, // destination: FactoryV1 contract
		big.NewInt(0),
		nil,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"deployUEA",
		abiUniversalAccount,
	)
}
```

**File:** x/uexecutor/keeper/deploy_uea.go (L1-46)
```go
package keeper

import (
	"context"

	sdk "github.com/cosmos/cosmos-sdk/types"
	evmtypes "github.com/cosmos/evm/x/vm/types"
	"github.com/ethereum/go-ethereum/common"
	"github.com/pushchain/push-chain-node/x/uexecutor/types"
)

// updateParams is for updating params collections of the module
func (k Keeper) DeployUEAV2(ctx context.Context, evmFrom common.Address, universalAccountId *types.UniversalAccountId) (*evmtypes.MsgEthereumTxResponse, error) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Debug("deploying UEA",
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

	k.Logger().Info("UEA deployed",
		"chain_namespace", universalAccountId.ChainNamespace,
		"chain_id", universalAccountId.ChainId,
		"owner", universalAccountId.Owner,
		"tx_hash", receipt.Hash,
		"gas_used", receipt.GasUsed,
	)

	return receipt, nil
}
```
