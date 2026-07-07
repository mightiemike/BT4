### Title
Non-Liquidatable `N_ACCOUNT` Accumulates Unbounded Perp Losses, Blocking User Withdrawals via `assertUtilization` — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`N_ACCOUNT` is a hardcoded privileged subaccount that is explicitly excluded from liquidation in `liquidateSubaccountImpl`. It can accumulate perp positions with unlimited losses through two protocol-native paths: (1) `clearNlpPoolPosition` transfers all losing positions from NLP pool subaccounts directly into `N_ACCOUNT`, and (2) `N_ACCOUNT` bypasses signature validation in `matchOrders`, allowing the sequencer to trade on its behalf without limit. Because `N_ACCOUNT` can never be liquidated, its losses are never resolved. When the sequencer settles `N_ACCOUNT`'s PnL via `settlePnl`, the negative PnL is converted into a deeply negative quote balance in `SpotEngine`, inflating total borrows. Once total borrows exceed total deposits, `assertUtilization` reverts inside `withdrawCollateral`, blocking all user withdrawals of the quote asset.

---

### Finding Description

**Step 1 — Liquidation is hardcoded off for `N_ACCOUNT`.**

In `liquidateSubaccountImpl`, before any liquidation logic runs, the following guard unconditionally rejects any attempt to liquidate `N_ACCOUNT`:

```solidity
require(
    txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
    ERR_NOT_LIQUIDATABLE
);
``` [1](#0-0) 

`N_ACCOUNT` is defined as the fixed sentinel `bytes32(0x...02)`: [2](#0-1) 

**Step 2 — `N_ACCOUNT` accumulates perp positions via `clearNlpPoolPosition`.**

The sequencer can call `clearNlpPoolPosition` to sweep all spot and perp balances from any NLP pool subaccount into `N_ACCOUNT`. If those positions carry negative `vQuoteBalance` (unrealized perp losses), they are transferred wholesale into `N_ACCOUNT`:

```solidity
perpEngine.updateBalance(
    productId,
    N_ACCOUNT,
    balance.amount,
    balance.vQuoteBalance   // ← negative PnL transferred in
);
``` [3](#0-2) 

**Step 3 — `N_ACCOUNT` also bypasses signature validation in `matchOrders`.**

In `_validateOrder`, `N_ACCOUNT` is explicitly whitelisted to skip the ECDSA signature check, meaning the sequencer can submit maker orders on behalf of `N_ACCOUNT` with no user authorization:

```solidity
(signedOrder.order.sender == N_ACCOUNT ||
    _checkSignature(...))
``` [4](#0-3) 

This allows `N_ACCOUNT` to accumulate perp positions through normal order matching, in addition to the `clearNlpPoolPosition` path.

**Step 4 — PnL settlement converts unrealized losses into a negative quote balance.**

`settlePnl` is a routine sequencer operation that calls `perpEngine.settlePnl` and credits the result to the subaccount's spot quote balance:

```solidity
function _settlePnl(bytes32 subaccount, uint256 productIds) internal {
    int128 amountSettled = perpEngine.settlePnl(subaccount, productIds);
    _spotEngine().updateBalance(QUOTE_PRODUCT_ID, subaccount, amountSettled);
}
``` [5](#0-4) 

When called for `N_ACCOUNT`, a large negative `amountSettled` drives `N_ACCOUNT`'s quote balance deeply negative, registering it as a large borrower in `SpotEngine`.

**Step 5 — `assertUtilization` blocks all user withdrawals.**

`withdrawCollateral` calls `spotEngine.assertUtilization(productId)` after every balance update: [6](#0-5) 

`assertUtilization` enforces that total borrows do not exceed total deposits for the product. Because `N_ACCOUNT`'s negative quote balance is counted as a borrow, and because `N_ACCOUNT` can never be liquidated to reduce that borrow, the utilization ratio can be driven above 100%. Once that threshold is crossed, every call to `withdrawCollateral` for the quote asset reverts, permanently blocking all user withdrawals until the sequencer manually injects collateral.

---

### Impact Explanation

Users who have deposited quote collateral and hold profitable positions cannot withdraw their funds. The protocol's core withdrawal path is broken for all users simultaneously, not just a single counterparty. The bad debt in `N_ACCOUNT` is never resolved by the liquidation engine because the liquidation guard is unconditional. This is a direct loss of access to funds and broken core functionality, matching the severity class of the reference report.

---

### Likelihood Explanation

Both accumulation paths are normal protocol operations: `clearNlpPoolPosition` is called by the sequencer as part of NLP pool lifecycle management, and `N_ACCOUNT` participates in order matching as the NLP liquidity provider. In a sustained adverse market move where NLP pools incur losses, `N_ACCOUNT` will accumulate negative PnL. PnL settlement is also a routine sequencer action. No attacker action is required; the vulnerability triggers through normal protocol operation under adverse market conditions.

---

### Recommendation

1. **Implement a socialization or insurance-fund backstop for `N_ACCOUNT`**: When `N_ACCOUNT`'s quote balance goes negative after PnL settlement, automatically draw from the insurance fund to cover the deficit before it inflates total borrows.
2. **Cap the positions that can be transferred into `N_ACCOUNT`** via `clearNlpPoolPosition` to an amount the insurance fund can cover.
3. **Alternatively**, introduce a dedicated liquidation path for `N_ACCOUNT` that does not transfer positions to a third-party liquidator but instead socializes the loss across NLP holders, preserving the design intent while preventing unbounded bad debt accumulation.

---

### Proof of Concept

```
1. NLP pool subaccount `nlpPool1` holds a large short perp position on product 3.
2. Price of product 3 rises 40%. `nlpPool1` now has vQuoteBalance = -500,000e18 (large unrealized loss).
3. Sequencer calls clearNlpPoolPosition(nlpPool1):
   - perpEngine.updateBalance(3, N_ACCOUNT, amount, -500_000e18)
   - N_ACCOUNT now holds the losing position.
4. N_ACCOUNT's maintenance health < 0. Any user calls liquidateSubaccount with liquidatee = N_ACCOUNT.
   → Reverts: ERR_NOT_LIQUIDATABLE (ClearinghouseLiq.sol:604-607).
5. Sequencer calls settlePnl([N_ACCOUNT], [productIds]):
   - perpEngine.settlePnl returns -500,000e18 for N_ACCOUNT.
   - spotEngine.updateBalance(QUOTE_PRODUCT_ID, N_ACCOUNT, -500_000e18).
   - N_ACCOUNT's quote balance = -500,000e18 (massive borrow).
6. Total borrows in SpotEngine now exceed total deposits.
7. Any user calls withdrawCollateral(productId=QUOTE_PRODUCT_ID, ...):
   - spotEngine.assertUtilization(QUOTE_PRODUCT_ID) reverts.
   - All user withdrawals are permanently blocked.
```

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L604-607)
```text
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
```

**File:** core/contracts/common/Constants.sol (L10-10)
```text
bytes32 constant N_ACCOUNT = 0x0000000000000000000000000000000000000000000000000000000000000002;
```

**File:** core/contracts/Clearinghouse.sol (L412-413)
```text
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);
```

**File:** core/contracts/Clearinghouse.sol (L617-627)
```text
    function _settlePnl(bytes32 subaccount, uint256 productIds) internal {
        IPerpEngine perpEngine = _perpEngine();

        int128 amountSettled = perpEngine.settlePnl(subaccount, productIds);

        _spotEngine().updateBalance(
            QUOTE_PRODUCT_ID,
            subaccount,
            amountSettled
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L799-810)
```text
            perpEngine.updateBalance(
                productId,
                subaccount,
                -balance.amount,
                -balance.vQuoteBalance
            );
            perpEngine.updateBalance(
                productId,
                N_ACCOUNT,
                balance.amount,
                balance.vQuoteBalance
            );
```

**File:** core/contracts/OffchainExchange.sol (L459-465)
```text
            (signedOrder.order.sender == N_ACCOUNT ||
                _checkSignature(
                    order.sender,
                    orderDigest,
                    linkedSigner,
                    signedOrder.signature
                )) &&
```
