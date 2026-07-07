### Title
Unprotected `initialize()` on `SpotEngine` and `PerpEngine` Allows Unauthorized Ownership Takeover — (File: `core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary

`SpotEngine` and `PerpEngine` inherit from `OwnableUpgradeable` (via `BaseEngine`) but define **no constructor** and therefore never call `_disableInitializers()`. Their public `initialize()` functions are callable by any unprivileged actor before the legitimate deployer, granting the attacker full ownership of the engine contracts and control over all risk parameters.

---

### Finding Description

Every other upgradeable contract in the Nado codebase that inherits from OpenZeppelin's `OwnableUpgradeable` protects its implementation with a constructor that calls `_disableInitializers()`:

- `Verifier` [1](#0-0) 
- `Airdrop` [2](#0-1) 
- `BaseWithdrawPool` [3](#0-2) 
- `ContractOwner` [4](#0-3) 
- `BaseProxyManager` [5](#0-4) 

`SpotEngine` and `PerpEngine` have **no constructor at all**. Their `initialize()` functions delegate to `BaseEngine._initialize()`, which carries the `initializer` modifier and calls `__Ownable_init()` followed by `transferOwnership(_admin)`:

```solidity
// SpotEngine.sol
function initialize(
    address _clearinghouse,
    address _offchainExchange,
    address _quote,
    address _endpoint,
    address _admin
) external {
    _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
    ...
}
``` [6](#0-5) 

```solidity
// BaseEngine.sol
function _initialize(...) internal initializer {
    __Ownable_init();
    setEndpoint(_endpointAddr);
    transferOwnership(_admin);
    _clearinghouse = IClearinghouse(_clearinghouseAddr);
    canApplyDeltas[_endpointAddr] = true;
    canApplyDeltas[_clearinghouseAddr] = true;
    canApplyDeltas[_offchainExchangeAddr] = true;
}
``` [7](#0-6) 

`PerpEngine` has the identical exposure: [8](#0-7) 

`BaseProxyManager` does **not** register `SpotEngine` or `PerpEngine` as managed proxies — only `Clearinghouse`, `ClearinghouseLiq`, `Endpoint`, and `EndpointTx` are managed there. [9](#0-8)  This is consistent with `ContractOwner` holding direct (non-proxy) references to both engines. [10](#0-9) 

**Two concrete attack surfaces arise:**

1. **Direct deployment (most likely):** Deployment and initialization are separate transactions. An attacker monitoring the mempool can front-run the `initialize()` call, supplying attacker-controlled `_admin`, `_clearinghouse`, `_offchainExchange`, and `_endpoint` addresses.

2. **Implementation-behind-proxy deployment:** The proxy's storage is initialized atomically at proxy construction, but the implementation contract's own `_initialized` slot is never set. An attacker can call `initialize()` directly on the implementation at any time after deployment, taking ownership of the implementation contract.

---

### Impact Explanation

An attacker who becomes owner of `SpotEngine` can immediately call `updateRisk()`:

```solidity
function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
    external
    onlyOwner
{
    _risk().value[productId] = riskStore;
}
``` [11](#0-10) 

Setting `longWeightInitial`, `longWeightMaintenance`, `shortWeightInitial`, or `shortWeightMaintenance` to adversarial values directly corrupts the health calculation for every subaccount holding that product. The attacker can:

- Set weights to zero or `2 * ONE` (which triggers the `return -INF` branch in `_calculateProductHealth`) to make all positions appear maximally undercollateralized, enabling mass liquidation of every user.
- Alternatively, inflate weights to prevent legitimate liquidations of insolvent positions, corrupting protocol solvency.

The attacker also controls `canApplyDeltas`, meaning they can revoke the legitimate clearinghouse's and endpoint's ability to update balances, effectively freezing the protocol.

---

### Likelihood Explanation

Front-running an initialization transaction is a well-known, low-skill attack on EVM chains with a public mempool. The attacker needs only to observe the deployment transaction and submit `initialize()` with a higher gas price. On chains with private mempools or sequencer-controlled ordering, the window is narrower but the implementation-contract attack path (scenario 2) remains open indefinitely with no time pressure.

---

### Recommendation

Add a constructor to both `SpotEngine` and `PerpEngine` that calls `_disableInitializers()`, matching the pattern used by every other upgradeable contract in the codebase:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
```

This is already the established pattern in `Verifier`, `Airdrop`, `BaseWithdrawPool`, `ContractOwner`, and `BaseProxyManager`.

---

### Proof of Concept

1. Attacker monitors the mempool for the `SpotEngine` deployment transaction.
2. Attacker submits `SpotEngine.initialize(attackerClearinghouse, attackerExchange, attackerQuote, attackerEndpoint, attackerAddress)` with higher gas, front-running the deployer.
3. `BaseEngine._initialize()` runs: `__Ownable_init()` sets `_owner = attackerAddress`; `canApplyDeltas[attackerEndpoint] = true`.
4. The legitimate deployer's `initialize()` call reverts because `_initialized == 1`.
5. Attacker calls `updateRisk(QUOTE_PRODUCT_ID, RiskStore{longWeightInitial: 0, shortWeightInitial: 2e9, ...})`.
6. All subaccounts with spot balances now compute `health = -INF` in `_calculateProductHealth`, making every account liquidatable.
7. Attacker liquidates all users via `Clearinghouse.liquidateSubaccount()`, draining protocol collateral.

### Citations

**File:** core/contracts/Verifier.sol (L36-39)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/Airdrop.sol (L19-21)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
```

**File:** core/contracts/BaseWithdrawPool.sol (L18-21)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/ContractOwner.sol (L27-30)
```text
    SpotEngine internal spotEngine;
    PerpEngine internal perpEngine;
    Endpoint internal endpoint;
    IClearinghouse internal clearinghouse;
```

**File:** core/contracts/ContractOwner.sol (L43-46)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
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

**File:** core/contracts/BaseProxyManager.sol (L102-105)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/SpotEngine.sol (L14-21)
```text
    function initialize(
        address _clearinghouse,
        address _offchainExchange,
        address _quote,
        address _endpoint,
        address _admin
    ) external {
        _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
```

**File:** core/contracts/BaseEngine.sol (L203-218)
```text
    function _initialize(
        address _clearinghouseAddr,
        address _offchainExchangeAddr,
        address _endpointAddr,
        address _admin
    ) internal initializer {
        __Ownable_init();
        setEndpoint(_endpointAddr);
        transferOwnership(_admin);

        _clearinghouse = IClearinghouse(_clearinghouseAddr);

        canApplyDeltas[_endpointAddr] = true;
        canApplyDeltas[_clearinghouseAddr] = true;
        canApplyDeltas[_offchainExchangeAddr] = true;
    }
```

**File:** core/contracts/BaseEngine.sol (L278-290)
```text
    function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
        external
        onlyOwner
    {
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance,
            ERR_BAD_PRODUCT_CONFIG
        );

        _risk().value[productId] = riskStore;
    }
```

**File:** core/contracts/PerpEngine.sol (L14-22)
```text
    function initialize(
        address _clearinghouse,
        address _offchainExchange,
        address,
        address _endpoint,
        address _admin
    ) external {
        _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
    }
```
