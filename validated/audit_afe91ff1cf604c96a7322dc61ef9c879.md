### Title
Immutable `deployer` Address in `onlyDeployer` Modifier Permanently Locks Critical Product Management Functions — (File: `core/contracts/ContractOwner.sol`)

---

### Summary
The `deployer` address in `ContractOwner` is assigned once during `initialize()` and has no update mechanism — not even callable by the contract owner (multisig). It is used in the `onlyDeployer` access modifier that gates five critical protocol management functions. If the deployer key becomes unavailable or must be rotated, all gated functions are permanently inaccessible with no on-chain recovery path.

---

### Finding Description
In `ContractOwner.sol`, the `deployer` state variable is set once during initialization:

```solidity
address internal deployer;
// ...
function initialize(...) external initializer {
    // ...
    deployer = _deployer;
}

modifier onlyDeployer() {
    require(msg.sender == deployer, "sender must be deployer");
    _;
}
```

No setter function exists anywhere in the contract to update `deployer`. The `onlyDeployer` modifier gates the following critical functions:

- `submitSpotAddOrUpdateProductCall` — queues spot product additions/updates
- `submitPerpAddOrUpdateProductCall` — queues perp product additions/updates
- `clearSpotAddOrUpdateProductCalls` — clears pending spot product call queue
- `clearPerpAddOrUpdateProductCalls` — clears pending perp product call queue
- `delistProduct` — submits a slow-mode delist transaction for a product

The contract owner (multisig) has no ability to update `deployer`. The `onlyOwner` and `onlyDeployer` roles are entirely separate, and the owner cannot reassign the deployer role. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

---

### Impact Explanation
If the deployer key is lost, compromised, or must be rotated:

1. **Product listing is permanently frozen**: `submitSpotAddOrUpdateProductCall` and `submitPerpAddOrUpdateProductCall` cannot be called, blocking all new product additions and risk parameter updates.
2. **Pending call queues cannot be cleared**: `clearSpotAddOrUpdateProductCalls` and `clearPerpAddOrUpdateProductCalls` are inaccessible, leaving the queue in a permanently dirty state that blocks `addOrUpdateProducts` (which requires the queue to be non-empty and matching).
3. **Products cannot be delisted**: `delistProduct` submits the slow-mode delist transaction. If this is inaccessible, a product that must be wound down (e.g., due to market conditions or oracle failure) cannot be delisted. Users holding positions in that product are left with no protocol-level settlement path.

The most concrete asset impact is the inability to delist products: users with open perp positions in a product that requires emergency delisting cannot have those positions settled at the delist price, leaving their collateral accounting in a broken state. [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation
Low-to-medium. The deployer is a single EOA or hot wallet used for operational product management. Key rotation is a standard operational need. Unlike the multisig owner, the deployer has no built-in rotation mechanism. The absence of a setter means any key rotation event — planned or forced — permanently breaks these functions. The protocol has no fallback.

---

### Recommendation
Add an `updateDeployer` function callable by the contract owner (multisig) to allow rotation of the deployer address:

```solidity
function updateDeployer(address newDeployer) external onlyOwner {
    require(newDeployer != address(0), "zero address");
    deployer = newDeployer;
}
```

This mirrors the pattern already used for `updateSubmitter` in `BaseProxyManager.sol`. [8](#0-7) 

---

### Proof of Concept

1. `ContractOwner.initialize()` is called with `_deployer = 0xDEPLOYER`. The `deployer` state variable is set to `0xDEPLOYER`.
2. The deployer key is lost or must be rotated (e.g., infrastructure compromise).
3. The multisig owner calls `updateDeployer(newAddr)` — **no such function exists**, call reverts.
4. Any call to `submitSpotAddOrUpdateProductCall`, `submitPerpAddOrUpdateProductCall`, `clearSpotAddOrUpdateProductCalls`, `clearPerpAddOrUpdateProductCalls`, or `delistProduct` reverts with `"sender must be deployer"` permanently.
5. A product requiring emergency delisting cannot be wound down. Users with open positions in that product have no settlement path through the protocol. [9](#0-8) [3](#0-2)

### Citations

**File:** core/contracts/ContractOwner.sol (L26-26)
```text
    address internal deployer;
```

**File:** core/contracts/ContractOwner.sol (L57-68)
```text
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
        spotEngine = SpotEngine(_spotEngine);
        perpEngine = PerpEngine(_perpEngine);
        endpoint = Endpoint(_endpoint);
        clearinghouse = IClearinghouse(_clearinghouse);
        verifier = Verifier(_verifier);
        wrappedNative = _wrappedNative;
    }
```

**File:** core/contracts/ContractOwner.sol (L70-73)
```text
    modifier onlyDeployer() {
        require(msg.sender == deployer, "sender must be deployer");
        _;
    }
```

**File:** core/contracts/ContractOwner.sol (L91-98)
```text
    function submitSpotAddOrUpdateProductCall(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        ISpotEngine.Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) external onlyDeployer {
```

**File:** core/contracts/ContractOwner.sol (L361-380)
```text
    function delistProduct(
        uint32[] calldata productIds,
        int128[] calldata pricesX18,
        bytes32[] calldata subaccounts
    ) external onlyDeployer {
        if (productIds.length != pricesX18.length) {
            revert InvalidInput();
        }
        for (uint256 i = 0; i < productIds.length; i++) {
            IEndpoint.DelistProduct memory _txn = IEndpoint.DelistProduct(
                productIds[i],
                pricesX18[i],
                subaccounts
            );
            _submitSlowModeTransaction(
                IEndpoint.TransactionType.DelistProduct,
                abi.encode(_txn)
            );
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L294-325)
```text
    function delistProduct(bytes calldata transaction) external onlyEndpoint {
        IEndpoint.DelistProduct memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.DelistProduct)
        );
        // only perp can be delisted
        require(
            productToEngine[txn.productId] == _perpEngine(),
            ERR_INVALID_PRODUCT
        );
        require(txn.priceX18 == _getPriceX18(txn.productId), ERR_INVALID_PRICE);
        IPerpEngine perpEngine = _perpEngine();
        for (uint256 i = 0; i < txn.subaccounts.length; i++) {
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                txn.productId,
                txn.subaccounts[i]
            );
            int128 baseDelta = -balance.amount;
            int128 quoteDelta = -baseDelta.mul(txn.priceX18);
            perpEngine.updateBalance(
                txn.productId,
                txn.subaccounts[i],
                baseDelta,
                quoteDelta
            );
            if (RiskHelper.isIsolatedSubaccount(txn.subaccounts[i])) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.subaccounts[i]);
            }
        }
    }
```

**File:** core/contracts/BaseProxyManager.sol (L181-183)
```text
    function updateSubmitter(address newSubmitter) external onlyOwner {
        submitter = newSubmitter;
    }
```
