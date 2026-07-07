### Title
Quote Token (USDC) Price Hardcoded to $1 — No Oracle Feed Used for Health and Deposit Checks - (File: `core/contracts/Clearinghouse.sol`, `core/contracts/SpotEngine.sol`)

---

### Summary

The Nado protocol hardcodes the price of its quote token (USDC, `QUOTE_PRODUCT_ID = 0`) to `ONE` (1e18 = $1) in two independent places: the initial risk store setup in `SpotEngine.initialize` and the `checkMinDeposit` function in `Clearinghouse`. No oracle feed is ever consulted for the quote token's price. During a USDC de-peg event, all health calculations and minimum deposit checks will overvalue USDC, allowing users to borrow against inflated collateral and avoid liquidation when they are truly undercollateralized.

---

### Finding Description

**Root cause 1 — `SpotEngine.initialize` hardcodes `priceX18: ONE` for the quote product:** [1](#0-0) 

The `RiskStore` for `QUOTE_PRODUCT_ID` is initialized with `priceX18: ONE`. This value is stored in the shared `RISK_STORAGE` slot and is read by `BaseEngine._calculateProductHealth` for every health computation involving the quote balance.

**Root cause 2 — `Clearinghouse.checkMinDeposit` explicitly bypasses any oracle for the quote product:** [2](#0-1) 

Even if the sequencer were to push an `UpdatePrice` transaction for product 0, `checkMinDeposit` ignores it and unconditionally uses `ONE` for the quote token. This is a hard-coded assumption baked into the logic, not a configuration choice.

**Health calculation path that consumes the hardcoded price:** [3](#0-2) 

`_calculateProductHealth` multiplies `amount * weight * risk.priceX18`. For the quote product, `risk.priceX18` is always `ONE` (never updated by any oracle), so the health contribution of a USDC balance is always `amount * 1.0 * 1.0 = amount`, regardless of USDC's true market price.

**Weights for the quote product are also set to full weight (1.0):** [1](#0-0) 

`longWeightInitial: 1e9` and `shortWeightInitial: 1e9` expand to `ONE` after the `* 1e9` scaling in `BaseEngine._risk()`, meaning USDC collateral receives 100% weight at $1 with no haircut.

---

### Impact Explanation

If USDC de-pegs (e.g., to $0.90 as occurred in March 2023):

1. A user deposits 1,000 USDC (true value: $900) as collateral via `Endpoint.depositCollateral` → `Clearinghouse.depositCollateral`.
2. `getHealth` calls `spotEngine.getHealthContribution` → `_calculateProductHealth`, which values the 1,000 USDC at $1,000 (using `priceX18 = ONE`).
3. The user can open positions or borrow against $1,000 of collateral when only $900 of real value backs it.
4. If the user's positions move against them, the protocol believes the account is healthy when it is actually undercollateralized by ~10%.
5. Liquidators cannot liquidate because `isUnderMaintenance` returns `false` — the health check uses the same hardcoded $1 price.

The protocol's insurance fund and NLP liquidity providers absorb the resulting bad debt. [4](#0-3) 

---

### Likelihood Explanation

USDC has experienced documented de-peg events (March 2023: ~$0.87 low). The vulnerability is reachable by any unprivileged user who deposits USDC collateral during such an event. No special permissions, governance capture, or sequencer compromise are required — the user simply deposits USDC and interacts with the protocol normally. The hardcoded assumption is structural, not conditional.

---

### Recommendation

1. Introduce a Chainlink USDC/USD price feed and query it in `checkMinDeposit` for the quote product instead of hardcoding `ONE`.
2. Allow the sequencer to push `UpdatePrice` transactions for `QUOTE_PRODUCT_ID` using a Chainlink feed, so health calculations reflect the real USDC price.
3. Apply a conservative lower-bound clamp (e.g., `min(oraclePrice, ONE)`) so that USDC is never valued *above* $1 but can be valued below it during de-peg events.
4. Consider adding a circuit breaker that pauses new borrows if the USDC oracle price falls below a threshold (e.g., $0.98).

---

### Proof of Concept

```
1. USDC de-pegs to $0.90 on-chain.

2. Attacker calls Endpoint.depositCollateral({
       productId: QUOTE_PRODUCT_ID (0),
       amount:    1_000_000_000  // 1,000 USDC (6 decimals)
   })
   → Clearinghouse.depositCollateral scales to 1_000e18 internal units
   → SpotEngine.updateBalance(0, attacker, +1_000e18)

3. Attacker calls Endpoint.submitTransactions([WithdrawCollateral({
       productId: some_non_quote_product,
       amount:    900e18  // borrow $900 worth of another asset
   })])
   → Clearinghouse.withdrawCollateral checks getHealth(attacker, INITIAL)
   → getHealth → spotEngine.getHealthContribution
   → _calculateProductHealth(0, attacker, INITIAL):
         risk.priceX18 = ONE  // hardcoded, not $0.90
         weight        = ONE  // 100%
         health       += 1_000e18 * ONE * ONE / ONE / ONE = 1_000e18  // valued at $1,000
   → Health = $1,000 - $900 = $100 > 0  ✓ (passes)
   → True health = $900 - $900 = $0  (borderline, any fee pushes negative)

4. USDC recovers to $1.00. Attacker repays $900 asset, keeps $100 profit.
   Protocol absorbed the de-peg risk with no compensation.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/SpotEngine.sol (L23-49)
```text
        configs[QUOTE_PRODUCT_ID] = Config({
            token: _quote,
            interestInflectionUtilX18: 8e17, // .8
            interestFloorX18: 1e16, // .01
            interestSmallCapX18: 4e16, // .04
            interestLargeCapX18: ONE, // 1
            withdrawFeeX18: ONE, // 1
            minDepositRateX18: 0 // 0
        });
        _risk().value[QUOTE_PRODUCT_ID] = RiskHelper.RiskStore({
            longWeightInitial: 1e9,
            shortWeightInitial: 1e9,
            longWeightMaintenance: 1e9,
            shortWeightMaintenance: 1e9,
            priceX18: ONE
        });
        _setState(
            QUOTE_PRODUCT_ID,
            State({
                cumulativeDepositsMultiplierX18: ONE,
                cumulativeBorrowsMultiplierX18: ONE,
                totalDepositsNormalized: 0,
                totalBorrowsNormalized: 0
            })
        );
        productIds.push(QUOTE_PRODUCT_ID);
        emit AddOrUpdateProduct(QUOTE_PRODUCT_ID);
```

**File:** core/contracts/Clearinghouse.sol (L698-715)
```text
    function checkMinDeposit(
        uint32 productId,
        uint128 amount,
        int256 minDepositAmount
    ) external returns (bool) {
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        uint8 decimals = _decimals(productId);
        require(decimals <= MAX_DECIMALS);

        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(multiplier) * int128(amount);
        int128 priceX18 = ONE;
        if (productId != QUOTE_PRODUCT_ID) {
            priceX18 = _getPriceX18(productId);
        }

        return priceX18.mul(amountRealized) >= minDepositAmount;
    }
```

**File:** core/contracts/BaseEngine.sol (L157-176)
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L51-58)
```text
    function isUnderMaintenance(bytes32 subaccount) internal returns (bool) {
        // Weighted maintenance health < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.MAINTENANCE
            ) < 0;
    }
```
