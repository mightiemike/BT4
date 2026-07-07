### Title
Minimum Deposit Amount Far Too Low Relative to Liquidation Fee Creates Economically Unliquidatable Dust Positions Leading to Bad Debt — (`core/contracts/common/Constants.sol`)

---

### Summary

`MIN_DEPOSIT_AMOUNT` ($0.1) and `MIN_FIRST_DEPOSIT_AMOUNT` ($5) are both far below the break-even collateral value (~$400) required for a liquidator to profit after paying the flat `LIQUIDATION_FEE` ($1). Any position whose collateral value falls below ~$400 is economically irrational to liquidate, so such positions accumulate as bad debt.

---

### Finding Description

In `Constants.sol`, three constants interact to create the vulnerability:

```
MIN_DEPOSIT_AMOUNT        = ONE / 10   = $0.10   (existing subaccounts)
MIN_FIRST_DEPOSIT_AMOUNT  = 5 * ONE   = $5.00   (new subaccounts)
LIQUIDATION_FEE           = 1e18      = $1.00   (flat fee charged to liquidator)
MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200 = 0.5%  (minimum liquidation discount)
LIQUIDATION_FEE_FRACTION  = 50%                 (fraction of discount to insurance)
``` [1](#0-0) [2](#0-1) [3](#0-2) 

**Liquidation discount mechanics** (`ClearinghouseStorage.sol` `getLiqPriceX18`): the minimum penalty applied to the liquidation price is `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%`. Of that discount, `LIQUIDATION_FEE_FRACTION = 50%` flows to the insurance fund, leaving the liquidator with at most `0.5% × 50% = 0.25%` of the position's oracle value as gross profit. [4](#0-3) 

**Liquidation fee deduction** (`EndpointTx.processTransactionImpl`): before calling `clearinghouse.liquidateSubaccount()`, the sequencer charges the liquidator a flat `LIQUIDATION_FEE = $1` via `chargeFee(signedTx.tx.sender, LIQUIDATION_FEE)`. [5](#0-4) 

**Net liquidator profit formula:**

```
net_profit = position_value × 0.25% − $1
```

Break-even: `position_value × 0.0025 = $1` → **position_value must be ≥ $400** for liquidation to be profitable.

The minimum deposit for an existing subaccount is $0.10 — **4,000× below break-even**. Even the minimum first deposit of $5 is **80× below break-even**. Any position whose collateral value is below $400 will never be liquidated by a rational actor.

The deposit path that creates these positions is `Endpoint.depositCollateral()` → `isValidDepositAmount()` → `clearinghouse.checkMinDeposit()`, which only enforces the $0.10 floor with no relationship to the $1 liquidation fee. [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**Impact: Medium**

Dust positions that fall below the liquidation break-even threshold (~$400) will never be liquidated. When their maintenance health drops below zero, the protocol accumulates bad debt. During finalization (`_finalizeSubaccount`), the insurance fund is drawn down to cover the negative quote balance of these positions. Repeated accumulation of many small bad-debt positions drains the insurance fund, ultimately socializing losses across all protocol participants via `perpEngine.socializeSubaccount`. [8](#0-7) 

---

### Likelihood Explanation

**Likelihood: Medium**

Any existing subaccount holder can call `Endpoint.depositCollateral()` with as little as $0.10 of any supported collateral token. If that collateral is a volatile asset and its price drops, the position becomes unhealthy. Because the $1 liquidation fee makes it unprofitable to liquidate positions below ~$400, these positions will persist indefinitely. This is a realistic scenario on any chain where gas costs are low and volatile assets are supported.

---

### Recommendation

Raise `MIN_DEPOSIT_AMOUNT` to a value that guarantees liquidation is always profitable. Given `LIQUIDATION_FEE = $1`, `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%`, and `LIQUIDATION_FEE_FRACTION = 50%`, the minimum collateral value at deposit must exceed the break-even threshold with a safety margin:

```
MIN_DEPOSIT_AMOUNT ≥ LIQUIDATION_FEE / (MIN_NON_SPREAD_LIQ_PENALTY_X18 × (1 − LIQUIDATION_FEE_FRACTION))
                   = $1 / (0.5% × 50%)
                   = $400
```

A value of at least `400 * ONE` (with additional margin for price decline between deposit and liquidation) should be enforced. Alternatively, replace the flat `LIQUIDATION_FEE` with a percentage-based fee scaled to position size.

---

### Proof of Concept

1. Alice is an existing subaccount holder. She calls `Endpoint.depositCollateral(subaccountName, volatileTokenProductId, minAmount)` where `minAmount` corresponds to $0.10 at current oracle price. `isValidDepositAmount` passes because `$0.10 >= MIN_DEPOSIT_AMOUNT ($0.10)`.

2. Alice borrows against this collateral (e.g., borrows $0.08 in quote given an 80% initial weight). Her initial health is marginally positive.

3. The volatile token price drops 20%. Alice's maintenance health becomes negative (`isUnderMaintenance` returns `true`).

4. Bob (liquidator) evaluates the liquidation:
   - Position value: ~$0.08
   - Gross discount at 0.5% minimum penalty: `$0.08 × 0.5% = $0.0004`
   - Liquidator's share (50%): `$0.0002`
   - Flat fee charged: `$1.00`
   - **Net profit: $0.0002 − $1.00 = −$0.9998** ← unprofitable

5. No liquidator submits a `LiquidateSubaccount` transaction. Alice's position remains open with negative maintenance health indefinitely.

6. The protocol accumulates bad debt. During eventual finalization, `_finalizeSubaccount` draws from the insurance fund to cover Alice's negative quote balance, depleting it for all users. [1](#0-0) [9](#0-8) [10](#0-9) [11](#0-10) [5](#0-4)

### Citations

**File:** core/contracts/common/Constants.sol (L27-27)
```text
int128 constant LIQUIDATION_FEE = 1e18; // $1
```

**File:** core/contracts/common/Constants.sol (L36-42)
```text
int128 constant LIQUIDATION_FEE_FRACTION = 500_000_000_000_000_000; // 50%

int128 constant INTEREST_FEE_FRACTION = 200_000_000_000_000_000; // 20%

int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/common/Constants.sol (L56-58)
```text
int128 constant MIN_SPREAD_LIQ_PENALTY_X18 = ONE / 400; // 0.25%

int128 constant MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200; // 0.5%
```

**File:** core/contracts/ClearinghouseStorage.sol (L41-59)
```text
    function getLiqPriceX18(uint32 productId, int128 amount)
        internal
        returns (int128, int128)
    {
        RiskHelper.Risk memory risk = IProductEngine(productToEngine[productId])
            .getRisk(productId);
        int128 penaltyX18 = (RiskHelper._getWeightX18(
            risk,
            amount,
            IProductEngine.HealthType.MAINTENANCE
        ) - ONE) / 5;
        if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
            if (penaltyX18 < 0) {
                penaltyX18 = -MIN_NON_SPREAD_LIQ_PENALTY_X18;
            } else {
                penaltyX18 = MIN_NON_SPREAD_LIQ_PENALTY_X18;
            }
        }
        return (risk.priceX18.mul(ONE + penaltyX18), risk.priceX18);
```

**File:** core/contracts/EndpointTx.sol (L408-410)
```text
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
```

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }
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

**File:** core/contracts/ClearinghouseLiq.sol (L386-412)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
```

**File:** core/contracts/ClearinghouseLiq.sol (L508-516)
```text
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
```
