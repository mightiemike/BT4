### Title
User Can Self-Liquidate Across Subaccounts to Capture Liquidation Discount — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

The `liquidateSubaccountImpl` function in `ClearinghouseLiq.sol` only prevents a subaccount from liquidating itself by checking `txn.sender != txn.liquidatee`. Because subaccounts are `bytes32` values encoding `address ++ subaccountName`, a single user can own multiple distinct subaccounts (same address prefix, different 12-byte name suffix) and use one to liquidate the other. This allows a user to self-liquidate across their own subaccounts, capturing the liquidation discount that would otherwise go to external liquidators.

---

### Finding Description

Subaccounts in Nado are `bytes32` values formed as `abi.encodePacked(msg.sender, subaccountName)` where `subaccountName` is a `bytes12` chosen freely by the user at deposit time. [1](#0-0) 

This means a single address can own an arbitrary number of distinct subaccounts (e.g., `address + "default"` and `address + "second"`). The signature validation in `Verifier.validateSignature` accepts any signature whose recovered address matches the first 20 bytes of the sender subaccount: [2](#0-1) 

So the same private key can validly sign transactions for all subaccounts belonging to the same address.

The self-liquidation guard in `liquidateSubaccountImpl` only compares the full `bytes32` values: [3](#0-2) 

It does **not** compare the address portions. A user controlling `address + "default"` (subA) and `address + "second"` (subB) can submit a `LiquidateSubaccount` transaction with `sender = subB` and `liquidatee = subA`. The check passes because `subB != subA` as `bytes32`, even though both are owned by the same address.

When the liquidation executes, the liquidator (subB) acquires the liquidated asset at the liquidation price, which is below oracle price by at least `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%`: [4](#0-3) 

The liquidation fee fraction is 50% of the spread: [5](#0-4) 

The liquidator (subB) pays `liquidationPayment + liquidationFees` and receives the asset. The liquidatee (subA) receives only `liquidationPayment` (below oracle value). The net effect is that the user moves their own asset from subA to subB at a discounted price, paying only the insurance fee, rather than losing the asset to an external liquidator.

---

### Impact Explanation

A user whose subA position becomes liquidatable can use subB to self-liquidate, effectively:

1. Retaining the asset (now in subB) instead of losing it to an external liquidator.
2. Clearing subA's undercollateralized position at the cost of only the insurance fee.
3. Capturing the 50% liquidation discount that would otherwise incentivize external liquidators.

This undermines the protocol's liquidation incentive mechanism. External liquidators are disincentivized if users can always front-run them by self-liquidating. In extreme cases (e.g., a large position approaching liquidation), the user can guarantee they keep their assets by pre-positioning a second subaccount.

The corrupted invariant is: **the liquidation discount must flow to an independent third party, not back to the position owner**. The `insurance` fund receives `liquidationFees`, but the user captures the other 50% of the spread via subB. [6](#0-5) 

---

### Likelihood Explanation

High. Creating a second subaccount requires only a deposit with a different `subaccountName`. This is a standard protocol feature with no additional cost beyond the deposit. Any user who anticipates their position becoming liquidatable can trivially set up a second subaccount in advance. The attack requires no privileged access, no oracle manipulation, and no external dependencies — only a valid signature from the user's own key.

---

### Recommendation

Extend the self-liquidation check to compare the **address portion** of `sender` and `liquidatee`, not just the full `bytes32`:

```solidity
require(
    address(uint160(bytes20(txn.sender))) != address(uint160(bytes20(txn.liquidatee))),
    ERR_UNAUTHORIZED
);
```

This prevents any user from liquidating any subaccount that shares their address prefix, regardless of the subaccount name suffix. [7](#0-6) 

---

### Proof of Concept

1. Alice deposits collateral into `address_alice + "default"` (subA) and opens a leveraged long position in BTC at oracle price $100.
2. Alice deposits a small amount into `address_alice + "second"` (subB) — a standard second subaccount.
3. BTC price drops; subA's maintenance health falls below 0 (liquidatable).
4. Alice signs a `LiquidateSubaccount` transaction: `sender = subB`, `liquidatee = subA`, `amount = 1 BTC`.
5. The sequencer processes it. `liquidateSubaccountImpl` checks `subB != subA` (passes), `isUnderMaintenance(subA)` (passes).
6. subA loses 1 BTC, receives $99.5 in quote (liquidation price). subB pays $99.75 (liquidation price + 50% fee), receives 1 BTC. Insurance receives $0.25.
7. Alice's combined position: she still holds 1 BTC (now in subB), subA's debt is cleared. An external liquidator would have taken the 1 BTC entirely. [8](#0-7)

### Citations

**File:** core/contracts/Endpoint.sol (L103-110)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
```

**File:** core/contracts/Verifier.sol (L291-304)
```text
    function validateSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        bytes memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L507-543)
```text
        } else if (engine == address(spotEngine)) {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);

            spotEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount
            );

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                v.liquidationPayment
            );

            spotEngine.updateBalance(txn.productId, txn.sender, txn.amount);

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationPayment - v.liquidationFees
            );

            if (txn.amount < 0) {
                insurance = spotEngine.updateQuoteFromInsurance(
                    txn.liquidatee,
                    insurance
                );
            }
```

**File:** core/contracts/ClearinghouseLiq.sol (L579-586)
```text
        insurance += v.liquidationFees;

        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-611)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );
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

**File:** core/contracts/common/Constants.sol (L36-36)
```text
int128 constant LIQUIDATION_FEE_FRACTION = 500_000_000_000_000_000; // 50%
```
