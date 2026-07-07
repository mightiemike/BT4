### Title
Deployer Can Front-Run Owner's `addOrUpdateProducts` to Substitute Malicious RiskStore Parameters — (`core/contracts/ContractOwner.sol`)

### Summary

The two-party commit scheme in `ContractOwner` is broken. The owner's confirmation in `addOrUpdateProducts` only validates `productId` (line 156), not the full `RiskStore`. Because the deployer controls both `clearSpotAddOrUpdateProductCalls` and `submitSpotAddOrUpdateProductCall`, they can atomically replace a pending call's `RiskStore` between the owner's off-chain review and on-chain execution. The owner's `spotIds` array — intended as an explicit approval — provides no protection against parameter substitution.

### Finding Description

`ContractOwner` uses two distinct privileged roles:

- **deployer** — set at initialization, controls `submitSpotAddOrUpdateProductCall` and `clearSpotAddOrUpdateProductCalls`
- **owner** (multisig) — controls `addOrUpdateProducts`, which is the final execution gate [1](#0-0) 

The intended invariant is that the owner reviews pending calls and explicitly approves them by passing `spotIds`. However, the only check performed at execution time is: [2](#0-1) 

This check binds only the `productId`, not `quoteId`, `sizeIncrement`, `minSize`, `config`, or `riskStore`. The deployer can therefore:

1. Submit a legitimate call with `productId=X` and a safe `RiskStore`.
2. Wait for the owner to observe the pending call and construct `spotIds=[X]`.
3. Front-run the owner's `addOrUpdateProducts` transaction by calling `clearSpotAddOrUpdateProductCalls()` followed immediately by `submitSpotAddOrUpdateProductCall(X, ..., maliciousRiskStore)`.
4. The owner's transaction executes: `spotIds[0] == call.productId` (X == X) passes, and `spotEngine.addOrUpdateProduct` is called with the deployer-substituted `maliciousRiskStore`. [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation

`SpotEngine.addOrUpdateProduct` writes the attacker-chosen `RiskStore` directly into `_risk().value[productId]`: [6](#0-5) 

With `longWeightInitial=0` or `priceX18` near zero, the Clearinghouse health calculation treats the product as worthless collateral, enabling undercollateralized borrowing. With `shortWeightInitial` inflated, short positions are under-margined. Both cases desynchronize risk state between `SpotEngine` and `Clearinghouse`, directly enabling the Critical scoped impact.

### Likelihood Explanation

The deployer is a distinct EOA or contract from the multisig owner. On any public mempool chain, the deployer can observe the owner's pending `addOrUpdateProducts` transaction and front-run it with a higher gas price. The attack requires no external dependencies, no leaked keys, and no social engineering — only the deployer acting within their granted on-chain permissions.

### Recommendation

The `addOrUpdateProducts` confirmation must commit to the full parameter set, not just `productId`. Two concrete fixes:

1. **Hash commitment**: Require the owner to pass a hash of the full `SpotAddOrUpdateProductCall` struct, and verify it against `keccak256(rawSpotAddOrUpdateProductCalls[i])` before executing.
2. **Snapshot at submission**: Record a block number or timestamp when each call is submitted, and require the owner to explicitly acknowledge the full encoded call (not just the ID) in `addOrUpdateProducts`.

### Proof of Concept

```solidity
// 1. Deployer submits legitimate call
contractOwner.submitSpotAddOrUpdateProductCall(
    productId, quoteId, sizeIncrement, minSize, config,
    RiskStore({longWeightInitial: 9e8, shortWeightInitial: 9e8,
               longWeightMaintenance: 8e8, shortWeightMaintenance: 8e8,
               priceX18: 1e18})
);

// 2. Owner sees pending call, prepares addOrUpdateProducts([productId], [])
// 3. Deployer front-runs:
contractOwner.clearSpotAddOrUpdateProductCalls();
contractOwner.submitSpotAddOrUpdateProductCall(
    productId, quoteId, sizeIncrement, minSize, config,
    RiskStore({longWeightInitial: 0, shortWeightInitial: 0,
               longWeightMaintenance: 0, shortWeightMaintenance: 0,
               priceX18: 1}) // near-zero price
);

// 4. Owner's tx executes — spotIds[0]==productId passes, malicious RiskStore registered
// Assert: spotEngine.getRisk(productId).longWeightInitial == 0  ✓ (attacker wins)
```

### Citations

**File:** core/contracts/ContractOwner.sol (L57-61)
```text
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
```

**File:** core/contracts/ContractOwner.sol (L91-115)
```text
    function submitSpotAddOrUpdateProductCall(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        ISpotEngine.Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) external onlyDeployer {
        uint32[] memory pendingIds = pendingSpotAddOrUpdateProductIds();
        for (uint256 i = 0; i < pendingIds.length; i++) {
            require(productId != pendingIds[i], "dup spot");
        }
        rawSpotAddOrUpdateProductCalls.push(
            abi.encode(
                SpotAddOrUpdateProductCall(
                    productId,
                    quoteId,
                    sizeIncrement,
                    minSize,
                    config,
                    riskStore
                )
            )
        );
    }
```

**File:** core/contracts/ContractOwner.sol (L139-141)
```text
    function clearSpotAddOrUpdateProductCalls() external onlyDeployer {
        delete rawSpotAddOrUpdateProductCalls;
    }
```

**File:** core/contracts/ContractOwner.sol (L156-156)
```text
            require(spotIds[i] == call.productId, "spot mismatch");
```

**File:** core/contracts/ContractOwner.sol (L157-164)
```text
            spotEngine.addOrUpdateProduct(
                call.productId,
                call.quoteId,
                call.sizeIncrement,
                call.minSize,
                call.config,
                call.riskStore
            );
```

**File:** core/contracts/SpotEngine.sol (L68-83)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;
```
