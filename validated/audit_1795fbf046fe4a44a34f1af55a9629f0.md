### Title
Unprotected `initialize()` Frontrun Grants Attacker Full Protocol Upgrade Control — (`File: core/contracts/BaseProxyManager.sol`)

---

### Summary

`BaseProxyManager.initialize()` has no caller restriction. Any unprivileged attacker who frontruns the deployment transaction becomes the `owner` and `submitter` of the `ProxyManager`, gaining the ability to upgrade every core protocol proxy (Clearinghouse, Endpoint, SpotEngine, PerpEngine) to a malicious implementation and drain all user funds.

---

### Finding Description

`BaseProxyManager.initialize()` is declared `external` with only the OpenZeppelin `initializer` modifier:

```solidity
// core/contracts/BaseProxyManager.sol:107-111
function initialize() external initializer {
    __Ownable_init();
    submitter = msg.sender;
    proxyManagerHelper = new ProxyManagerHelper();
}
```

`__Ownable_init()` sets `msg.sender` as the contract owner. There is no `require(msg.sender == expectedDeployer)` guard — compare with `ContractOwner.initialize()` which correctly enforces `require(_deployer == msg.sender, "expected deployed to initialize")`.

`ProxyManager` is deployed as a `TransparentUpgradeableProxy`. The implementation constructor calls `_disableInitializers()`, which protects the bare implementation but leaves the proxy's storage uninitialized until `initialize()` is called in a **separate transaction**. An attacker who monitors the mempool can frontrun that second transaction.

Once the attacker controls `owner` and `submitter`, they have access to:

| Function | Access | Effect |
|---|---|---|
| `migrateAll()` | `onlyOwner` | Upgrades all registered proxies to attacker-supplied implementations |
| `forceMigrateSelf()` | `onlyOwner` | Upgrades the ProxyManager proxy itself |
| `registerRegularProxy()` | `onlyOwner` | Registers attacker-controlled addresses as protocol proxies |
| `submitImpl()` | `onlySubmitter` | Queues malicious implementations for all named contracts |
| `updateSubmitter()` | `onlyOwner` | Permanently locks out the legitimate deployer |

The `ProxyManagerHelper` is also created with the attacker as its `proxyManager`, so `upgradeClearinghouseLiq()` and `upgradeEndpointTx()` are also attacker-controlled.

---

### Impact Explanation

**Impact: Critical.** The attacker gains the upgrade key for the entire Nado protocol. By calling `migrateAll()` with malicious implementations of Clearinghouse, Endpoint, SpotEngine, and PerpEngine, the attacker can insert arbitrary logic — including functions that transfer all deposited collateral to the attacker's address. Every user's deposited funds across all products are at risk.

---

### Likelihood Explanation

**Likelihood: Medium.** The attack requires mempool monitoring and a frontrun. This is a well-understood technique on EVM chains with a public mempool (Ethereum mainnet, Arbitrum with public sequencer fallback). The deployment transaction is a one-time, observable event. No special privilege or leaked key is required — any EOA can execute it.

---

### Recommendation

Add a deployer check to `BaseProxyManager.initialize()`, mirroring the pattern already used in `ContractOwner.initialize()`:

```solidity
function initialize(address _expectedDeployer) external initializer {
    require(msg.sender == _expectedDeployer, "unauthorized initializer");
    __Ownable_init();
    submitter = msg.sender;
    proxyManagerHelper = new ProxyManagerHelper();
}
```

Alternatively, use a factory pattern or `CREATE2` + atomic initialization so that `initialize()` is called within the same transaction as the proxy deployment, making frontrunning impossible.

---

### Proof of Concept

1. Deployer broadcasts a transaction to call `ProxyManager.initialize()` on the newly deployed proxy.
2. Attacker sees this in the mempool and submits the same call with higher gas.
3. Attacker's `initialize()` executes first: `owner = attacker`, `submitter = attacker`.
4. Deployer's `initialize()` reverts (already initialized).
5. Attacker calls `registerRegularProxy("Clearinghouse", clearinghouseProxy)` to register the real proxy.
6. Attacker deploys `MaliciousClearinghouse` with a `drain()` function that transfers all quote token balances to the attacker.
7. Attacker calls `submitImpl("Clearinghouse", maliciousClearinghouse)` (as submitter).
8. Attacker calls `migrateAll([{name: "Clearinghouse", impl: maliciousClearinghouse}])` (as owner).
9. All subsequent Clearinghouse calls execute against the malicious implementation. Attacker calls `drain()` and withdraws all user collateral.

**Root cause line:** [1](#0-0) 

**Contrast with protected pattern in ContractOwner:** [2](#0-1) 

**Owner-gated upgrade path that attacker gains:** [3](#0-2) 

**Submitter-gated impl submission attacker gains:** [4](#0-3)

### Citations

**File:** core/contracts/BaseProxyManager.sol (L107-111)
```text
    function initialize() external initializer {
        __Ownable_init();
        submitter = msg.sender;
        proxyManagerHelper = new ProxyManagerHelper();
    }
```

**File:** core/contracts/BaseProxyManager.sol (L189-201)
```text
    function migrateAll(NewImpl[] calldata newImpls) external onlyOwner {
        for (uint32 i = 0; i < newImpls.length; i++) {
            if (_isEqual(newImpls[i].name, CLEARINGHOUSE_LIQ)) {
                _migrateClearinghouseLiq(newImpls[i]);
            } else if (_isEqual(newImpls[i].name, ENDPOINT_TX)) {
                _migrateEndpointTx(newImpls[i]);
            } else {
                _migrateRegularProxy(newImpls[i]);
            }
            codeHashes[newImpls[i].name] = pendingHashes[newImpls[i].name];
        }
        require(!hasPending(), "still having pending impls to be migrated.");
    }
```

**File:** core/contracts/ContractOwner.sol (L57-59)
```text
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
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
