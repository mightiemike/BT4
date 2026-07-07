### Title
Taker Fee Underpayment Due to Stale Price Used in `alreadyMatched` Calculation — (`core/contracts/OffchainExchange.sol`)

---

### Summary

When a taker order is filled across multiple sequential fills at different prices, the `alreadyMatched` value passed to `applyFee()` is computed by multiplying the **current** maker's price against the total base amount already filled (`filledAmounts`). This approximation is incorrect when prices have moved between fills: it does not reflect the actual quote already matched. In a falling-price market, this underestimates `alreadyMatched`, shrinks the metered fee base, and causes the taker to pay less in trading fees than the protocol intends.

---

### Finding Description

In `OffchainExchange.sol`, when a taker order is partially filled across multiple sequential `matchOrders` calls, the protocol tracks only the **base amount** already filled: [1](#0-0) 

At each subsequent fill, `alreadyMatched` (the quote amount already matched, used to determine how much of the current fill exceeds `market.minSize` and is thus subject to the fee rate) is reconstructed by multiplying the stored base amount by the **current** maker's price: [2](#0-1) 

Inside `applyFee()`, this value determines the metered fee base: [3](#0-2) 

The logic computes `feeApplied` as the portion of the current fill that, when added to `alreadyMatched`, exceeds `market.minSize`. If `alreadyMatched` is underestimated (because the current price is lower than the prices at which prior fills occurred), then `|alreadyMatched + matchQuote|` is smaller than the true total quote matched, `feeApplied` is reduced, and the taker's fee is lower than intended.

The root cause is that `filledAmounts` stores only base units: [1](#0-0) 

...while the actual quote already matched at historical prices is never stored. The protocol has no `filledQuoteAmounts` mapping. The current price is used as a proxy for all historical fill prices, which is incorrect whenever price has moved.

---

### Impact Explanation

- **Accounting corruption**: The protocol collects less fee revenue than intended on multi-fill taker orders when prices fall between fills.
- **Concrete state delta**: `sequencerFee[productId]` (and `market.collectedFees`) accumulate less than the fee rate implies. The taker's `quoteDelta` is reduced by a smaller fee, leaving more quote in the taker's subaccount than intended.
- **Bounded but real**: The underpayment scales with (a) the price drop between fills, (b) the base amount already filled, and (c) the fee rate. In a volatile market with large orders split across many fills, this can be material.

---

### Likelihood Explanation

- Any unprivileged trader can trigger this by placing a large taker order that is filled incrementally across multiple `matchOrders` calls (the sequencer fills orders in batches).
- Price movement between fills is routine in any active market.
- No special permissions, governance access, or external dependencies are required. The sequencer submits fills in normal operation; the taker does not need to do anything beyond placing a standard order.

---

### Recommendation

Replace the base-amount-only `filledAmounts` tracking with a dual mapping that also tracks the **quote amount** already matched for each taker digest:

```solidity
mapping(bytes32 => int128) public filledQuoteAmounts;
```

Pass `filledQuoteAmounts[ordersInfo.taker.digest]` (negated for buy takers) directly as `alreadyMatched` instead of recomputing it from `filledAmounts * currentPrice`. Update `filledQuoteAmounts` alongside `filledAmounts` after each fill using the actual `quoteDelta` of the fill (before fee deduction), so that the true historical quote matched is always available.

---

### Proof of Concept

1. Taker places a buy order for 10 units at price ≤ $110. `market.minSize = $500`.
2. **Fill 1**: Maker offers 4 units at $100. `matchQuote = -$400`. `alreadyMatched = 0` (first fill). `feeApplied = max(0, |0 + (-400)| - 500) = 0`. No fee charged on this fill (below minSize). `filledAmounts[digest] = 4`.
3. Price drops. **Fill 2**: Maker offers 6 units at $90. `matchQuote = -$540`.
   - **Correct** `alreadyMatched` = `-$400` (actual quote paid in fill 1).
   - **Actual** `alreadyMatched` = `-maker.order.priceX18.mul(filledAmounts)` = `-$90 * 4 = -$360`.
   - **Correct** `feeApplied = min(|(-400) + (-540)| - 500, 540) = min(440, 540) = 440`.
   - **Actual** `feeApplied = min(|(-360) + (-540)| - 500, 540) = min(400, 540) = 400`.
   - At a 2 bps fee rate, the taker underpays by `(440 - 400) * 0.0002 = $0.008` on this fill.
4. With larger orders and higher volatility, the underpayment scales proportionally. The protocol's `sequencerFee` and `collectedFees` are permanently short by the difference. [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/OffchainExchange.sol (L536-544)
```text
            int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) -
                market.minSize;
            feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
            if (feeApplied > 0) {
                if (matchQuote < 0) {
                    feeApplied = -feeApplied;
                }
                meteredQuote += feeApplied;
            }
```

**File:** core/contracts/OffchainExchange.sol (L556-565)
```text
        int128 keepRateX18 = ONE - feeInfo.feeRate;
        int128 newMeteredQuote = (meteredQuote > 0)
            ? meteredQuote.mul(keepRateX18)
            : meteredQuote.div(keepRateX18);
        orderInfo.fee = meteredQuote - newMeteredQuote;
        orderInfo.builderFee = matchQuote.abs().mul(feeInfo.builderFeeRate);
        orderInfo.quoteDelta =
            orderInfo.quoteDelta -
            orderInfo.fee -
            orderInfo.builderFee;
```

**File:** core/contracts/OffchainExchange.sol (L769-777)
```text
        // apply the taker fee
        applyFee(
            callState.productId,
            ordersInfo.taker,
            market,
            -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
            taker.order.appendix,
            true
        );
```

**File:** core/contracts/OffchainExchange.sol (L831-835)
```text
        if (taker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.taker.digest] += ordersInfo
                .taker
                .amountDelta;
        }
```

**File:** core/contracts/common/Constants.sol (L21-21)
```text
int128 constant TAKER_SEQUENCER_FEE = 0; // $0.00
```
