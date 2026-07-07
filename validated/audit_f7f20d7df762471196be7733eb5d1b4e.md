### Title
Missing `__gap` Storage Variable in All Stateful Upgradeable Base Contracts Enables Storage Slot Corruption on Upgrade - (File: core/contracts/EndpointStorage.sol)

### Summary
None of the Nado protocol's stateful base contracts include a `__gap` storage variable. Without it, any future upgrade that adds new state variables to these inherited contracts will shift the storage slots of all subsequent variables, silently corrupting the protocol's live state. The most critical exposure is `EndpointStorage` (shared between `Endpoint` and `EndpointTx` via `delegatecall`) and `ClearinghouseStorage` (shared between `Clearinghouse` and `ClearinghouseLiq` via `delegatecall`).

### Finding Description
The Nado protocol uses a transparent proxy pattern for upgradeability and a `delegatecall` dispatch pattern for logic separation. Multiple stateful base contracts that are inherited by proxy-deployed contracts contain no `__gap` variable:

- `EndpointStorage` — inherited by both `Endpoint` (proxy) and `EndpointTx` (delegatecall target). [1](#0-0) 
- `ClearinghouseStorage` — inherited by both `Clearinghouse` (proxy) and `ClearinghouseLiq` (delegatecall target). [2](#0-1) 
- `EndpointGated` — inherited by `Clearinghouse`, `OffchainExchange`, `BaseEngine` (and thus `SpotEngine`, `PerpEngine`). [3](#0-2) 
- `BaseEngine` — inherited by `SpotEngineState` and `PerpEngineState`. [4](#0-3) 
- `BaseProxyManager`, `BaseWithdrawPool`, `ContractOwner`, `Verifier` — all upgradeable, all stateful, all missing `__gap`. [5](#0-4) 

The `delegatecall` dispatch pattern makes this especially dangerous. `Endpoint` delegates all transaction processing to `EndpointTx`:

```solidity
(bool success, bytes memory result) = endpointTx.delegatecall(callData);
``` [6](#0-5) 

`EndpointTx` is independently upgradeable via `upgradeEndpointTx`: [7](#0-6) 

Both `Endpoint` and `EndpointTx` inherit `EndpointStorage` in the same position in their inheritance chain. If a developer adds a new state variable to `EndpointStorage` in a new `EndpointTx` implementation but not in `Endpoint`, the `delegatecall` will read and write the wrong storage slots in `Endpoint`'s storage.

The same pattern applies to `Clearinghouse` delegating to `ClearinghouseLiq`: [8](#0-7) 

`ClearinghouseLiq` is independently upgradeable via `upgradeClearinghouseLiq`: [9](#0-8) 

### Impact Explanation
If a developer adds a new variable to `EndpointStorage` in a new `EndpointTx` implementation, every subsequent storage slot in `Endpoint` shifts. The corrupted variables include:

- `clearinghouse`, `spotEngine`, `perpEngine`, `sequencer`, `offchainExchange`, `verifier`, `endpointTx` — all read as wrong addresses, breaking every protocol operation
- `nonces` — corrupted mapping enables replay attacks on all signed transactions (withdrawals, liquidations, link-signer)
- `linkedSigners` — corrupted mapping allows unauthorized signers to act on behalf of subaccounts
- `priceX18` — corrupted price mapping breaks all health checks, liquidations, and settlements
- `slowModeTxs` — corrupted queue breaks all slow-mode deposits and withdrawals [10](#0-9) 

For `ClearinghouseStorage`, corruption of `insurance`, `productToEngine`, `engineByType`, and `spreads` would break liquidations, collateral accounting, and spread health calculations. [11](#0-10) 

### Likelihood Explanation
The Nado protocol is actively upgraded. The codebase already contains a `reinitializer(2)` in `BaseProxyManager.bootstrapEndpointTx`, confirming that multi-version upgrades have already occurred. [12](#0-11)  The `ProxyManager` exposes `submitImpl` and `migrateAll` for routine upgrades of `EndpointTx` and `ClearinghouseLiq`. [13](#0-12)  Any future upgrade that adds a variable to `EndpointStorage` or `ClearinghouseStorage` — a natural evolution for a growing protocol — will silently corrupt live state with no on-chain warning.

### Recommendation
Add `uint256[50] private __gap;` as the last variable in each stateful base contract:
- `EndpointStorage`
- `ClearinghouseStorage`
- `EndpointGated`
- `BaseEngine`
- `SpotEngineState`
- `PerpEngineState`
- `BaseProxyManager`
- `BaseWithdrawPool`
- `ContractOwner`
- `Verifier`

The gap size should be chosen so that the total number of storage slots used by the contract plus the gap equals a round number (e.g., 50 or 100), following the OpenZeppelin convention.

### Proof of Concept
1. Developer prepares a new `EndpointTx` implementation that adds `address internal newFeeCollector;` to `EndpointStorage` before the existing variables.
2. Owner calls `ProxyManager.migrateAll([{ENDPOINT_TX, newImpl}])`, which calls `Endpoint.upgradeEndpointTx(newImpl)`.
3. Any subsequent call to `Endpoint.submitTransactionsChecked(...)` triggers `_delegatecallEndpointTx(...)`.
4. Inside the delegatecall, `EndpointTx` reads `clearinghouse` from slot `M+2` (shifted by 1), but `Endpoint`'s storage has `spotEngine` at that slot.
5. `clearinghouse.depositCollateral(txn)` is called on the `spotEngine` address, reverting or silently corrupting state.
6. All user deposits, withdrawals, liquidations, and order matching are broken. Corrupted `nonces` and `linkedSigners` mappings additionally enable replay attacks and unauthorized signer substitution on all previously valid signed transactions.

### Citations

**File:** core/contracts/EndpointStorage.sol (L19-66)
```text
abstract contract EndpointStorage {
    using ERC20Helper for IERC20Base;

    IClearinghouse public clearinghouse;
    ISpotEngine internal spotEngine;
    IPerpEngine internal perpEngine;
    ISanctionsList internal sanctions;

    address internal sequencer;
    int128 internal sequencerFees;

    mapping(bytes32 => uint64) internal subaccountIds;
    mapping(uint64 => bytes32) internal subaccounts;
    uint64 internal numSubaccounts;

    mapping(address => uint64) internal nonces;

    uint64 public nSubmissions;

    IEndpoint.SlowModeConfig internal slowModeConfig;
    mapping(uint64 => IEndpoint.SlowModeTx) internal slowModeTxs;

    struct Times {
        uint128 perpTime;
        uint128 spotTime;
    }

    Times internal times;

    mapping(uint32 => int128) internal sequencerFee;

    mapping(bytes32 => address) internal linkedSigners;

    mapping(bytes32 => address) internal nlpSigners;
    IEndpoint.NlpPool[] public nlpPools;

    int128 internal slowModeFees;

    // invitee -> referralCode
    mapping(address => string) public referralCodes; // deprecated

    mapping(uint32 => int128) internal priceX18;
    address internal offchainExchange;

    IVerifier internal verifier;

    address internal endpointTx;

```

**File:** core/contracts/ClearinghouseStorage.sol (L8-30)
```text
abstract contract ClearinghouseStorage {
    using MathSD21x18 for int128;

    // Each clearinghouse has a quote ERC20
    address internal quote;
    address internal clearinghouse;
    address internal clearinghouseLiq;

    // product ID -> engine address
    mapping(uint32 => IProductEngine) internal productToEngine;
    // Type to engine address
    mapping(IProductEngine.EngineType => IProductEngine) internal engineByType;
    // Supported engine types
    IProductEngine.EngineType[] internal supportedEngines;

    int128 internal insurance;

    int128 internal lastLiquidationFees;

    uint256 internal spreads;

    address internal withdrawPool;

```

**File:** core/contracts/EndpointGated.sol (L10-11)
```text
abstract contract EndpointGated is OwnableUpgradeable, IEndpointGated {
    address private endpoint;
```

**File:** core/contracts/BaseEngine.sol (L15-27)
```text
abstract contract BaseEngine is IProductEngine, EndpointGated {
    using MathSD21x18 for int128;

    IClearinghouse internal _clearinghouse;
    uint32[] internal productIds;

    mapping(address => bool) internal canApplyDeltas;

    // subaccount -> bitmapIndex -> bitmapChunk
    mapping(bytes32 => mapping(uint32 => uint256)) internal nonZeroBalances;

    bytes32 internal constant RISK_STORAGE = keccak256("nado.protocol.risk");

```

**File:** core/contracts/BaseProxyManager.sol (L74-88)
```text
abstract contract BaseProxyManager is OwnableUpgradeable {
    string internal constant CLEARINGHOUSE = "Clearinghouse";
    string internal constant CLEARINGHOUSE_LIQ = "ClearinghouseLiq";
    string internal constant ENDPOINT = "Endpoint";
    string internal constant ENDPOINT_TX = "EndpointTx";

    address public submitter;
    ProxyManagerHelper internal proxyManagerHelper;

    string[] internal contractNames;
    mapping(string => address) public proxies;
    mapping(string => address) public pendingImpls;
    mapping(string => bytes32) public pendingHashes;
    mapping(string => bytes32) public codeHashes;

```

**File:** core/contracts/BaseProxyManager.sol (L126-145)
```text
    function bootstrapEndpointTx(address _endpointTx, bytes32 expectedCodeHash)
        external
        onlyOwner
        reinitializer(2)
    {
        require(_isEndpointRegistered(), "Endpoint not registered");
        require(_endpointTx != address(0), "EndpointTx is zero");
        require(_endpointTx.code.length > 0, "EndpointTx has no code");

        bytes32 actualCodeHash = _getCodeHash(_endpointTx.code);
        require(actualCodeHash == expectedCodeHash, "EndpointTx hash mismatch");

        require(_getEndpointTxImpl() == address(0), "EndpointTx already set");

        proxyManagerHelper.upgradeEndpointTx(_endpointTx);

        pendingImpls[ENDPOINT_TX] = _endpointTx;
        pendingHashes[ENDPOINT_TX] = actualCodeHash;
        codeHashes[ENDPOINT_TX] = actualCodeHash;
    }
```

**File:** core/contracts/Endpoint.sol (L68-84)
```text
    function _delegatecallEndpointTx(bytes memory callData)
        internal
        returns (bytes memory)
    {
        require(endpointTx != address(0), "Endpoint Tx not set");
        (bool success, bytes memory result) = endpointTx.delegatecall(callData);
        if (!success) {
            if (result.length == 0) {
                revert();
            }
            // solhint-disable-next-line no-inline-assembly
            assembly {
                revert(add(result, 0x20), mload(result))
            }
        }
        return result;
    }
```

**File:** core/contracts/Endpoint.sol (L368-375)
```text
    function upgradeEndpointTx(address _endpointTx) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        endpointTx = _endpointTx;
    }
```

**File:** core/contracts/Clearinghouse.sol (L648-662)
```text
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```

**File:** core/contracts/Clearinghouse.sol (L677-684)
```text
    function upgradeClearinghouseLiq(address _clearinghouseLiq) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        clearinghouseLiq = _clearinghouseLiq;
    }
```

**File:** core/contracts/ProxyManager.sol (L16-24)
```text
    function submitImpl(string memory name, address impl)
        external
        override
        onlySubmitter
    {
        require(pendingImpls[name] != address(0), "unsupported contract.");
        pendingImpls[name] = impl;
        pendingHashes[name] = _getCodeHash(impl.code);
    }
```
