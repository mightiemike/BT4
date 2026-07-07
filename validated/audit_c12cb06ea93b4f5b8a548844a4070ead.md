### Title
NLP Oracle Price Updated in Engine but Not Persisted to `Endpoint.priceX18`, Causing Stale Price in Downstream Consumers — (File: `core/contracts/Clearinghouse.sol`, `core/contracts/BaseEngine.sol`, `core/contracts/EndpointTx.sol`)

---

### Summary

The Nado protocol maintains **two separate price stores** for each product: `Endpoint.priceX18[productId]` (read by `Endpoint.getPriceX18()`) and `_risk().value[productId].priceX18` inside each engine (read by health calculations). When `mintNlp` or `burnNlp` is processed, the engine's price store for `NLP_PRODUCT_ID` is updated with the sequencer-supplied `oraclePriceX18`, but `Endpoint.priceX18[NLP_PRODUCT_ID]` is **never written**. Any subsequent call to `Endpoint.getPriceX18(NLP_PRODUCT_ID)` — including the `checkMinDeposit` path and liquidation price calculations in `ClearinghouseLiq` — silently reads the stale pre-mint/burn price.

---

### Finding Description

**Two price stores and how they are updated:**

`UpdatePrice` transactions (processed in `EndpointTx`) update **both** stores atomically:

```
clearinghouse.updatePrice(transaction)   // → engine._risk().value[productId].priceX18
priceX18[productId] = newPriceX18;       // → Endpoint.priceX18[productId]
``` [1](#0-0) [2](#0-1) 

`mintNlp` and `burnNlp`, however, call `spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18)` directly — updating only the engine's risk store — and **never touch `Endpoint.priceX18[NLP_PRODUCT_ID]`**:

```solidity
spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);  // engine store updated
// Endpoint.priceX18[NLP_PRODUCT_ID] ← NOT written
``` [3](#0-2) [4](#0-3) 

**The stale read path:**

`Clearinghouse._getPriceX18()` always reads from `Endpoint.priceX18`:

```solidity
function _getPriceX18(uint32 productId) internal returns (int128) {
    return IEndpoint(getEndpoint()).getPriceX18(productId);
}
``` [5](#0-4) 

`Endpoint.getPriceX18()` reads the stale mapping:

```solidity
_priceX18 = priceX18[productId];
``` [6](#0-5) 

`_getPriceX18` is consumed by `checkMinDeposit` (used during collateral deposits) and by liquidation price calculations in `ClearinghouseLiq`:

```solidity
priceX18 = _getPriceX18(productId);
return priceX18.mul(amountRealized) >= minDepositAmount;
``` [7](#0-6) 

**Health calculations are unaffected** because `BaseEngine._calculateProductHealth()` reads `_risk(productId).priceX18` directly from the engine store — the one that IS updated by `mintNlp`/`burnNlp`. The divergence is therefore invisible to health checks but visible to every consumer of `Endpoint.getPriceX18(NLP_PRODUCT_ID)`. [8](#0-7) 

---

### Impact Explanation

Between a `mintNlp`/`burnNlp` call and the next sequencer-issued `UpdatePrice` transaction for `NLP_PRODUCT_ID`, `Endpoint.priceX18[NLP_PRODUCT_ID]` is stale:

- **`checkMinDeposit`**: If NLP is accepted as collateral via a direct deposit path, the minimum-deposit USD threshold is evaluated against the old price. A rising NLP price means the check under-values the deposit (too permissive); a falling price over-values it (incorrectly blocking valid deposits).
- **Liquidation pricing** (`ClearinghouseLiq`): Liquidation prices and fees for NLP spot positions are derived from `_getPriceX18(NLP_PRODUCT_ID)`. A stale lower price allows a liquidator to acquire NLP at below-market value, causing direct financial loss to the liquidatee. A stale higher price prevents legitimate liquidations, leaving the protocol under-collateralised. [9](#0-8) 

---

### Likelihood Explanation

`mintNlp` and `burnNlp` are sequencer-submitted transactions that occur in normal protocol operation whenever users mint or redeem NLP. The window of divergence is the gap between such a call and the next `UpdatePrice` for `NLP_PRODUCT_ID`. Because `UpdatePrice` is a separate sequencer transaction (not atomically bundled with `mintNlp`/`burnNlp`), the window is non-zero and predictable. A liquidator monitoring on-chain state can observe the divergence and time a liquidation call to exploit the stale price. No privileged access is required to trigger a liquidation.

---

### Recommendation

In `Clearinghouse.mintNlp()` and `burnNlp()`, after calling `spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18)`, also persist the price to `Endpoint.priceX18` via a dedicated setter (analogous to how `EndpointTx` writes `priceX18[productId] = newPriceX18` after `UpdatePrice`). This ensures both price stores remain in sync and `Endpoint.getPriceX18(NLP_PRODUCT_ID)` always reflects the most recently used oracle price. [1](#0-0) 

---

### Proof of Concept

1. Sequencer submits `UpdatePrice` for `NLP_PRODUCT_ID` with price `P0`. Both `Endpoint.priceX18[NLP_PRODUCT_ID]` and `_risk().value[NLP_PRODUCT_ID].priceX18` are set to `P0`.
2. NLP price rises. Sequencer submits `mintNlp` with `oraclePriceX18 = P1 > P0`. `_risk().value[NLP_PRODUCT_ID].priceX18` is updated to `P1`; `Endpoint.priceX18[NLP_PRODUCT_ID]` remains `P0`.
3. Before the next `UpdatePrice` transaction, a liquidator calls the liquidation entry point targeting a subaccount with an NLP spot position.
4. `ClearinghouseLiq` calls `_getPriceX18(NLP_PRODUCT_ID)` → `Endpoint.getPriceX18(NLP_PRODUCT_ID)` → returns stale `P0`.
5. Liquidation price and fees are computed using `P0 < P1`. The liquidatee's NLP is transferred at the stale lower price; the liquidator captures the `P1 - P0` spread as unearned profit at the liquidatee's expense. [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/EndpointTx.sol (L486-492)
```text
        } else if (txType == IEndpoint.TransactionType.UpdatePrice) {
            (uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(
                transaction
            );
            if (productId != 0) {
                priceX18[productId] = newPriceX18;
            }
```

**File:** core/contracts/BaseEngine.sol (L162-176)
```text
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
```

**File:** core/contracts/BaseEngine.sol (L273-276)
```text
    function updatePrice(uint32 productId, int128 priceX18) external virtual {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
        _risk().value[productId].priceX18 = priceX18;
    }
```

**File:** core/contracts/Clearinghouse.sol (L358-375)
```text
    function updatePrice(bytes calldata transaction)
        external
        onlyEndpoint
        returns (uint32, int128)
    {
        IEndpoint.UpdatePrice memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.UpdatePrice)
        );
        require(txn.priceX18 > 0, ERR_INVALID_PRICE);
        IProductEngine engine = productToEngine[txn.productId];
        if (address(engine) != address(0)) {
            engine.updatePrice(txn.productId, txn.priceX18);
            return (txn.productId, txn.priceX18);
        } else {
            return (0, 0);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L694-696)
```text
    function _getPriceX18(uint32 productId) internal returns (int128) {
        return IEndpoint(getEndpoint()).getPriceX18(productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L709-714)
```text
        int128 priceX18 = ONE;
        if (productId != QUOTE_PRODUCT_ID) {
            priceX18 = _getPriceX18(productId);
        }

        return priceX18.mul(amountRealized) >= minDepositAmount;
```

**File:** core/contracts/Endpoint.sol (L338-341)
```text
    {
        _priceX18 = priceX18[productId];
        require(_priceX18 != 0, ERR_INVALID_PRODUCT);
        emit PriceQuery(productId);
```
