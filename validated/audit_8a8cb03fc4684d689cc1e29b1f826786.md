### Title
NLP Token Lock Period Prevents Liquidators from Immediately Exiting Received NLP Collateral — (`core/contracts/Clearinghouse.sol`, `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

When a liquidator liquidates a subaccount holding NLP tokens as spot collateral, the liquidator's subaccount receives NLP tokens via `spotEngine.updateBalance`. However, `burnNlp` enforces a 4-day lock period via `getNlpUnlockedBalance`, preventing the liquidator from immediately converting those NLP tokens back to USDC. This mirrors the external report's root cause: a liquidator receives an asset during liquidation but is blocked from exiting it through the only available redemption path due to an access restriction (here, a time-lock rather than a whitelist).

---

### Finding Description

The liquidation flow in `ClearinghouseLiq._handleLiquidationPayment` handles spot product liquidations by transferring the liquidated spot balance directly to the liquidator's subaccount:

```solidity
spotEngine.updateBalance(txn.productId, txn.liquidatee, -txn.amount);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee, v.liquidationPayment);
spotEngine.updateBalance(txn.productId, txn.sender, txn.amount);   // liquidator receives spot
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -v.liquidationPayment - v.liquidationFees);
``` [1](#0-0) 

When `txn.productId == NLP_PRODUCT_ID (11)`, the liquidator receives NLP tokens in their subaccount. NLP tokens contribute to health (evidenced by the `getHealth` check in `mintNlp` passing after NLP is credited), meaning they carry a non-zero `longWeightInitialX18` and are eligible for liquidation per the `_assertLiquidationAmount` guard:

```solidity
require(
    spotEngine.getRisk(spotId).longWeightInitialX18 != 0,
    ERR_INVALID_PRODUCT
);
``` [2](#0-1) 

The only exit path for NLP tokens is `burnNlp` in `Clearinghouse.sol`, which enforces:

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
``` [3](#0-2) 

The lock period constant is:

```solidity
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
``` [4](#0-3) 

NLP tokens received via `spotEngine.updateBalance` during liquidation are subject to the same lock period tracked by the SpotEngine, because the lock is enforced at the `burnNlp` call site regardless of how the NLP balance was credited. A liquidator who receives NLP tokens during liquidation cannot call `burnNlp` until the 4-day lock expires.

---

### Impact Explanation

A flash-loan-based liquidator — the most common and capital-efficient liquidation strategy — must repay the flash loan within the same transaction or block. After receiving NLP tokens via liquidation, the liquidator cannot immediately burn them for USDC to repay the loan. The liquidator is left holding illiquid NLP tokens for up to 4 days, with no alternative on-chain exit path (there is no NLP-to-USDC transfer function; `transferQuote` only moves quote balances; NLP cannot be directly withdrawn via `withdrawCollateral` as a redeemable token).

This makes NLP position liquidations economically unattractive or outright impossible for the most common liquidator archetype. During periods of intense price action — exactly when liquidations are most urgently needed — the pool of willing liquidators for NLP collateral is severely reduced, increasing the risk of bad debt accumulation in the protocol. [5](#0-4) 

---

### Likelihood Explanation

NLP is a first-class spot product in the protocol (product ID 11), explicitly minted and tracked as subaccount collateral. Any subaccount holding NLP as collateral that falls below maintenance health is eligible for liquidation. The liquidation path through `liquidateSubaccountImpl` → `_handleLiquidationPayment` is the standard, sequencer-triggered flow. The lock period restriction in `burnNlp` is unconditional — it applies to all callers regardless of how they acquired NLP tokens. No special attacker capability is required; any liquidator who liquidates an NLP position triggers this condition. [6](#0-5) 

---

### Recommendation

When NLP tokens are transferred to a liquidator's subaccount via `spotEngine.updateBalance` during a liquidation event, the SpotEngine should either:

1. **Exempt liquidation-sourced NLP from the lock period** — track the acquisition source in the SpotEngine's NLP balance record and skip the lock check when the source is a liquidation credit; or
2. **Allow immediate burn of liquidation-received NLP** — add a dedicated `burnNlpFromLiquidation` path that bypasses the lock period check for balances credited via `ClearinghouseLiq`.

Alternatively, the protocol could restrict NLP from being used as liquidatable collateral (set `longWeightInitialX18 = 0` for NLP) and handle NLP-collateralized positions through a separate, lock-aware settlement mechanism.

---

### Proof of Concept

1. User A mints NLP tokens via `mintNlp`, receiving NLP balance in their subaccount.
2. Market moves adversely; User A's maintenance health drops below zero.
3. Liquidator B (a smart contract using a flash loan) calls `liquidateSubaccount` with `productId = NLP_PRODUCT_ID`.
4. `_handleLiquidationPayment` executes: `spotEngine.updateBalance(11, txn.sender, txn.amount)` — Liquidator B's subaccount now holds NLP tokens.
5. Liquidator B immediately submits a `BurnNlp` transaction to convert NLP → USDC to repay the flash loan.
6. `burnNlp` calls `spotEngine.getNlpUnlockedBalance(txn.sender)` — returns 0 because the NLP was just received and the 4-day lock has not elapsed.
7. Transaction reverts with `ERR_UNLOCKED_NLP_INSUFFICIENT`.
8. Liquidator B cannot repay the flash loan; the liquidation strategy is non-viable. [7](#0-6) 
<cite repo="Thankgoddavid56/nado-contracts--019" path="core/contracts/ClearinghouseLiq.sol" start="529

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L159-163)
```text
        } else if (engine == address(spotEngine)) {
            require(
                spotEngine.getRisk(spotId).longWeightInitialX18 != 0,
                ERR_INVALID_PRODUCT
            );
```

**File:** core/contracts/ClearinghouseLiq.sol (L518-536)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
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

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
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

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```
