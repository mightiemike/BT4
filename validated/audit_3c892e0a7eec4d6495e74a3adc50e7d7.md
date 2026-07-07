### Title
Stale Engine Address Cache in `OffchainExchange` and `Endpoint` Due to One-Time Initialization from `Clearinghouse` — (`core/contracts/OffchainExchange.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

`OffchainExchange` and `Endpoint` each cache `spotEngine` and `perpEngine` addresses at initialization time by querying `Clearinghouse.getEngineByType()`. `Clearinghouse.addEngine()` is the function that actually populates those addresses. If `addEngine` is called after either consumer contract is initialized, the cached engine addresses remain `address(0)` with no recovery path. Neither `OffchainExchange` nor `Endpoint` exposes any function to re-sync the cached values.

---

### Finding Description

During `OffchainExchange.initialize()`, the contract snapshots the engine addresses from `Clearinghouse`:

```solidity
spotEngine = ISpotEngine(
    clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
);
perpEngine = IPerpEngine(
    clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
);
``` [1](#0-0) 

`Endpoint.initialize()` performs the identical snapshot:

```solidity
spotEngine = ISpotEngine(
    clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
);
perpEngine = IPerpEngine(
    clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
);
``` [2](#0-1) 

The source of truth for those addresses is `Clearinghouse.addEngine()`, which is the only function that writes to `engineByType`:

```solidity
function addEngine(
    address engine,
    address offchainExchange,
    IProductEngine.EngineType engineType
) external onlyOwner {
    require(address(engineByType[engineType]) == address(0));
    ...
    engineByType[engineType] = productEngine;
``` [3](#0-2) 

`addEngine` also calls `productEngine.initialize(address(this), offchainExchange, ...)`, which means `OffchainExchange` must already be deployed before `addEngine` is called. This creates a natural window where `OffchainExchange.initialize()` can be (and in a straightforward deployment, will be) called before `addEngine` populates the Clearinghouse's `engineByType` mapping. When that happens, both `spotEngine` and `perpEngine` resolve to `address(0)` in the consumer contracts.

Neither `OffchainExchange` nor `Endpoint` provides any setter or re-sync function for these cached values. The `initializer` modifier prevents re-initialization. The `addEngine` guard (`require(address(engineByType[engineType]) == address(0))`) prevents a second call, so there is no on-chain recovery path. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

The most concrete breakage is in `OffchainExchange.requireEngine()`:

```solidity
function requireEngine() internal virtual {
    require(
        msg.sender == address(spotEngine) ||
            msg.sender == address(perpEngine),
        "only engine can modify config"
    );
}
``` [6](#0-5) 

`requireEngine()` gates `updateMarket`, which is called by `BaseEngine._addOrUpdateProduct` every time a product is added or updated:

```solidity
_exchange().updateMarket(productId, quoteId, sizeIncrement, minSize);
``` [7](#0-6) 

If `spotEngine` and `perpEngine` are `address(0)`, `requireEngine()` permanently reverts for every caller, making it impossible to ever register a product in `OffchainExchange`. The entire trading surface of the protocol — spot and perp order matching — is permanently disabled with no on-chain fix.

For `Endpoint`, the cached `spotEngine` and `perpEngine` are consumed by `EndpointTx` logic via `delegatecall`, which reads from `Endpoint`'s storage slots. Stale `address(0)` values there would cause all `EndpointTx` paths that touch engine state to revert. [8](#0-7) 

---

### Likelihood Explanation

The deployment sequence that triggers this is the natural one: deploy `OffchainExchange` (so its address can be passed to `addEngine`), then call `Clearinghouse.addEngine`. If `OffchainExchange.initialize` is called in that window — which is the expected flow since `OffchainExchange` must exist before `addEngine` is called — the desynchronization occurs. There is no on-chain ordering constraint that prevents it, and no post-deployment correction mechanism.

---

### Recommendation

Replace the one-time snapshot with a live lookup, or add an owner-callable sync function in both `OffchainExchange` and `Endpoint`:

```solidity
function syncEngines() external onlyOwner {
    spotEngine = ISpotEngine(
        clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
    );
    perpEngine = IPerpEngine(
        clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
    );
}
```

Alternatively, remove the local cache entirely and always resolve engine addresses dynamically through `clearinghouse.getEngineByType()` at call time, as `Clearinghouse` itself does via `_spotEngine()` / `_perpEngine()` helpers. [9](#0-8) 

---

### Proof of Concept

1. Deploy `Clearinghouse` (no engines registered yet; `engineByType[SPOT] == address(0)`).
2. Deploy `OffchainExchange` proxy and call `OffchainExchange.initialize(clearinghouse, endpoint)`.
   - `clearinghouse.getEngineByType(SPOT)` → `address(0)` → `spotEngine = address(0)`.
   - `clearinghouse.getEngineByType(PERP)` → `address(0)` → `perpEngine = address(0)`.
3. Deploy `SpotEngine` and `PerpEngine`.
4. Call `Clearinghouse.addEngine(spotEngine, offchainExchange, SPOT)` — Clearinghouse now has the real `spotEngine`, but `OffchainExchange.spotEngine` is still `address(0)`.
5. Call `Clearinghouse.addEngine(perpEngine, offchainExchange, PERP)` — same desync for `perpEngine`.
6. Attempt to add a product: `SpotEngine.addOrUpdateProduct(...)` → calls `_exchange().updateMarket(...)` → calls `OffchainExchange.updateMarket(...)` → `requireEngine()` checks `msg.sender == address(0)` → **permanent revert**.
7. No recovery: `addEngine` cannot be called again (zero-check guard); `OffchainExchange.initialize` cannot be called again (`initializer` modifier); no setter exists. [6](#0-5) [10](#0-9)

### Citations

**File:** core/contracts/OffchainExchange.sol (L32-33)
```text
    ISpotEngine internal spotEngine;
    IPerpEngine internal perpEngine;
```

**File:** core/contracts/OffchainExchange.sol (L252-257)
```text
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
```

**File:** core/contracts/OffchainExchange.sol (L260-266)
```text
    function requireEngine() internal virtual {
        require(
            msg.sender == address(spotEngine) ||
                msg.sender == address(perpEngine),
            "only engine can modify config"
        );
    }
```

**File:** core/contracts/Endpoint.sol (L47-52)
```text
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
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

**File:** core/contracts/Clearinghouse.sol (L156-166)
```text
    function addEngine(
        address engine,
        address offchainExchange,
        IProductEngine.EngineType engineType
    ) external onlyOwner {
        require(address(engineByType[engineType]) == address(0));
        require(engine != address(0));
        IProductEngine productEngine = IProductEngine(engine);
        // Register
        supportedEngines.push(engineType);
        engineByType[engineType] = productEngine;
```

**File:** core/contracts/EndpointStorage.sol (L23-24)
```text
    ISpotEngine internal spotEngine;
    IPerpEngine internal perpEngine;
```

**File:** core/contracts/BaseEngine.sol (L263-263)
```text
        _exchange().updateMarket(productId, quoteId, sizeIncrement, minSize);
```

**File:** core/contracts/ClearinghouseStorage.sol (L1-1)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
```
