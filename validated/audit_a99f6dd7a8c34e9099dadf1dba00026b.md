### Title
Stale Prices for Non-NLP Products in `mintNlp`/`burnNlp` Health Checks Allow Undercollateralized Positions - (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.mintNlp()` and `Clearinghouse.burnNlp()` each update only the NLP product price before executing a full cross-product health check. Because the health check evaluates every product in the subaccount using the price stored in `_risk().value[productId].priceX18`, any non-NLP product whose price has not been refreshed by a recent `UpdatePrice` transaction is evaluated at a stale value. A user whose non-NLP positions have declined in value can therefore pass the health gate while their actual collateral ratio is below the required threshold.

---

### Finding Description

`Clearinghouse.mintNlp()` calls `spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18)` and then immediately calls `getHealth(txn.sender, IProductEngine.HealthType.INITIAL)`. [1](#0-0) 

`Clearinghouse.burnNlp()` does the same: it calls `spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18)` and then calls `getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE)`. [2](#0-1) 

`getHealth()` aggregates health across all products held by the subaccount by calling `spotEngine.getHealthContribution()` and `perpEngine.getHealthContribution()`. [3](#0-2) 

Each engine's `_calculateProductHealth()` multiplies the position amount by `risk.priceX18`, which is the value last written by `BaseEngine.updatePrice()`. [4](#0-3) 

`BaseEngine.updatePrice()` is only called when the sequencer submits an `UpdatePrice` transaction; it updates exactly one product at a time. [5](#0-4) 

Because `mintNlp`/`burnNlp` refresh only `NLP_PRODUCT_ID`, every other product in the subaccount (spot assets, perp positions) is evaluated at whatever price was last written by a prior `UpdatePrice` transaction. If those prices are stale — for example, because a spot or perp asset dropped sharply between two sequencer price-update batches — the health check will overstate the subaccount's collateral value and may pass when it should fail.

---

### Impact Explanation

**`burnNlp`**: The explicit purpose of the terminal health check is stated in the code comment: *"This check prevents malicious actors from deliberately creating unhealthy subaccounts through NLP burns."* [6](#0-5) 

If non-NLP prices are stale and inflated, a user with a perp or spot position that has declined in value can burn NLP and receive quote while the maintenance health check passes. The user extracts quote from a position that is actually below the maintenance threshold, creating an undercollateralized subaccount that may subsequently require socialization or insurance coverage.

**`mintNlp`**: A user whose non-NLP positions are already below the initial health threshold (at current market prices) can mint NLP — depositing quote and receiving NLP tokens — because the initial health check passes on stale prices. This allows the user to acquire NLP tokens without satisfying the true collateral requirement.

---

### Likelihood Explanation

Prices in Nado are updated one product at a time via sequencer-submitted `UpdatePrice` transactions. During periods of high market volatility, the window between a price move and the corresponding on-chain price update can be several seconds to minutes. A user who has a signed `MintNlp` or `BurnNlp` transaction queued can have it processed by the sequencer during this window. Because the contract itself does not enforce that all held-product prices are current before the health check, the structural condition for the bug is always present; it is activated whenever market prices diverge from stored engine prices, which is a routine occurrence.

---

### Recommendation

Before executing the health check in `mintNlp` and `burnNlp`, require that all products held by the subaccount have had their prices updated within the current transaction batch, or alternatively require the caller to supply fresh prices for every product in the subaccount (analogous to the recommendation in the reference report: *"require all collateral prices to be updated"*). At minimum, the sequencer should be required to submit `UpdatePrice` transactions for all products held by the subaccount immediately before processing `MintNlp` or `BurnNlp`.

---

### Proof of Concept

1. Alice holds a subaccount with:
   - A long BTC perp position (product ID `P`) worth $100,000 at the current market price of $40,000/BTC.
   - 10 NLP tokens.
   - The engine's stored price for `P` is $50,000 (stale — the last `UpdatePrice` for `P` was processed before the price drop).

2. Alice signs a `BurnNlp` transaction burning her 10 NLP tokens. The sequencer includes it in a batch without first submitting an `UpdatePrice` for `P`.

3. `burnNlp` calls `spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18)` — only the NLP price is refreshed.

4. `getHealth(alice, MAINTENANCE)` is called. `_calculateProductHealth` for product `P` computes `amount × weight × risk.priceX18` using the stale $50,000 price, overstating Alice's health by $10,000 × weight.

5. The maintenance health check passes. Alice receives quote tokens from the NLP burn.

6. With the actual BTC price of $40,000, Alice's maintenance health is negative. She has extracted quote from an undercollateralized subaccount, leaving a shortfall that must be covered by the insurance fund or socialized across depositors.

### Citations

**File:** core/contracts/Clearinghouse.sol (L71-139)
```text
    function getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
        public
        returns (int128 health)
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);

        uint256 _spreads = spreads;
        while (_spreads != 0) {
            uint32 _spotId = uint32(_spreads & 0xFF);
            _spreads >>= 8;
            uint32 _perpId = uint32(_spreads & 0xFF);
            _spreads >>= 8;

            IProductEngine.CoreRisk memory perpCoreRisk = perpEngine
                .getCoreRisk(subaccount, _perpId, healthType);

            if (perpCoreRisk.amount == 0) {
                continue;
            }

            IProductEngine.CoreRisk memory spotCoreRisk = spotEngine
                .getCoreRisk(subaccount, _spotId, healthType);

            if (
                (spotCoreRisk.amount == 0) ||
                ((spotCoreRisk.amount > 0) == (perpCoreRisk.amount > 0))
            ) {
                continue;
            }

            int128 basisAmount;
            if (spotCoreRisk.amount > 0) {
                basisAmount = MathHelper.min(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            } else {
                basisAmount = -MathHelper.max(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            }

            // spreads have 5x higher leverage than the underlying products.
            // but it's capped at 100x leverage at most.
            int128 existingWeight = (spotCoreRisk.longWeight +
                perpCoreRisk.longWeight) / 2;
            int128 spreadWeight = RiskHelper._getSpreadWeightX18(
                perpCoreRisk,
                spotCoreRisk,
                healthType
            );

            health += basisAmount
                .mul(spotCoreRisk.price + perpCoreRisk.price)
                .mul(spreadWeight - existingWeight);
            emit PriceQuery(_spotId);
            emit PriceQuery(_perpId);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L461-482)
```text
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
```

**File:** core/contracts/Clearinghouse.sol (L493-529)
```text
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
```

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
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
    }
```

**File:** core/contracts/BaseEngine.sol (L273-276)
```text
    function updatePrice(uint32 productId, int128 priceX18) external virtual {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
        _risk().value[productId].priceX18 = priceX18;
    }
```
