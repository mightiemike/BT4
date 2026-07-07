### Title
`_validateOrder` Does Not Use `MarketInfo` Parameter to Enforce Minimum Order Size — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`_validateOrder` in `OffchainExchange.sol` accepts a `MarketInfo memory` parameter containing the market's `minSize` and `sizeIncrement` constraints, but the parameter is explicitly unnamed and never referenced inside the function body. As a result, no on-chain check enforces that an order's amount meets the minimum size requirement. This is a direct structural analog to the GMX `_validateRange` bug: a validation function receives the boundary data it needs but performs no comparison against it.

---

### Finding Description

In `OffchainExchange.sol`, `_validateOrder` is the central on-chain order validation gate called from `matchOrders`. Its signature is:

```solidity
function _validateOrder(
    CallState memory callState,
    MarketInfo memory,          // ← unnamed, never used
    IEndpoint.SignedOrder memory signedOrder,
    bytes32 orderDigest,
    bool isTaker,
    address linkedSigner
) internal view returns (bool) {
``` [1](#0-0) 

The `MarketInfo` struct carries `minSize` and `sizeIncrement` — the two market-level constraints that bound valid order amounts. Inside `_validateOrder`, the function checks order version, maker/taker flags, reduce-only logic, price positivity, signature validity, non-zero amount, and expiration — but never reads `minSize` or `sizeIncrement` from the unnamed `MarketInfo` argument. [2](#0-1) 

The only downstream use of `minSize` is inside `applyFee` for flat-fee metering, not for order rejection: [3](#0-2) 

The only downstream use of `sizeIncrement` is a silent truncation in `matchOrders`:

```solidity
ordersInfo.taker.amountDelta -= ordersInfo.taker.amountDelta % market.sizeIncrement;
``` [4](#0-3) 

Neither constitutes a validation gate. An order with `amount < minSize` or `amount` not aligned to `sizeIncrement` passes `_validateOrder` without error.

`minSize` and `sizeIncrement` are set per-product at product registration time in both `SpotEngine.addOrUpdateProduct` and `PerpEngine.addOrUpdateProduct`, confirming they are intended protocol-level constraints: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

The on-chain invariant that orders must be at least `minSize` is never enforced. Concretely:

1. **Fee accounting corruption**: `applyFee` assumes the taker's `matchQuote` is at least `minSize` when computing the flat-fee component. For a sub-`minSize` order, `feeApplied` becomes negative and is suppressed by the `if (feeApplied > 0)` guard, meaning the protocol charges only the flat `minSize` fee on a trade that is smaller than `minSize`. This corrupts the fee-to-volume ratio and undercharges the taker.

2. **Zero-amount match**: If `order.amount < sizeIncrement`, the truncation `amountDelta -= amountDelta % sizeIncrement` produces `amountDelta = 0`. The match proceeds, balance updates are no-ops, but `filledAmounts` and events are still emitted, polluting protocol state with phantom fills.

3. **Protocol invariant bypass**: The minimum order size exists to prevent dust trades and ensure fee revenue covers operational costs. Its absence from on-chain enforcement means the constraint is only as strong as the sequencer's off-chain logic.

---

### Likelihood Explanation

The sequencer submits `matchOrders` and is the proximate actor. However, the sequencer's off-chain matching engine is a separate software layer. A bug or misconfiguration in that layer — or a future upgrade that relaxes off-chain checks — would silently pass through to on-chain execution with no safety net, because `_validateOrder` provides none. The unnamed `MarketInfo` parameter is a clear code-level signal that the intended check was never implemented.

---

### Recommendation

Inside `_validateOrder`, name the `MarketInfo` parameter and add explicit size checks:

```solidity
function _validateOrder(
    CallState memory callState,
    MarketInfo memory market,   // name it
    IEndpoint.SignedOrder memory signedOrder,
    ...
) internal view returns (bool) {
    ...
    int128 remainingAmount = order.amount.abs();
    if (remainingAmount < market.minSize) return false;
    if (market.sizeIncrement > 0 && remainingAmount % market.sizeIncrement != 0) return false;
    ...
}
```

---

### Proof of Concept

1. Admin registers a perp product with `minSize = 1e18` (1 unit) via `PerpEngine.addOrUpdateProduct`.
2. Trader signs an order with `amount = 1` (far below `minSize`).
3. Sequencer (or a sequencer with an off-chain bug) calls `Endpoint.submitTransactionsChecked` → `matchOrders`.
4. `_validateOrder` is called with the `MarketInfo` containing `minSize = 1e18`, but the parameter is unnamed and unused — the function returns `true`.
5. `matchOrders` proceeds: `amountDelta = 1 % sizeIncrement` truncates to `0`; `applyFee` charges only the flat `minSize` fee on a zero-value trade; `filledAmounts` is updated with `0`; events are emitted for a phantom fill.
6. The minimum order size invariant is violated with no revert. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L410-418)
```text
    function _validateOrder(
        CallState memory callState,
        MarketInfo memory,
        IEndpoint.SignedOrder memory signedOrder,
        bytes32 orderDigest,
        bool isTaker,
        address linkedSigner
    ) internal view returns (bool) {
        if ((signedOrder.order.appendix & 255) != orderVersion()) {
```

**File:** core/contracts/OffchainExchange.sol (L457-469)
```text
        return
            ((order.priceX18 > 0) || _isTWAP(order.appendix)) &&
            (signedOrder.order.sender == N_ACCOUNT ||
                _checkSignature(
                    order.sender,
                    orderDigest,
                    linkedSigner,
                    signedOrder.signature
                )) &&
            // valid amount
            (order.amount != 0) &&
            !_expired(order.expiration);
    }
```

**File:** core/contracts/OffchainExchange.sol (L524-544)
```text
        if (taker) {
            // flat minimum fee
            if (alreadyMatched == 0) {
                meteredQuote += market.minSize;
                if (matchQuote < 0) {
                    meteredQuote = -meteredQuote;
                }
            }

            // exclude the portion on [0, self.min_size) for match_quote and
            // add to metered_quote
            // fee is only applied on [minSize, quote_amount)
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

**File:** core/contracts/OffchainExchange.sol (L631-645)
```text
    function matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
        external
        onlyEndpoint
    {
        CallState memory callState = _getCallState(txn.matchOrders.productId);

        OrdersInfo memory ordersInfo;

        MarketInfo memory market = getMarketInfo(callState.productId);
        IEndpoint.SignedOrder memory taker = txn.matchOrders.taker;
        IEndpoint.SignedOrder memory maker = txn.matchOrders.maker;

        // isolated subaccounts cannot be used as sender
        require(
            !RiskHelper.isIsolatedSubaccount(taker.order.sender),
```

**File:** core/contracts/OffchainExchange.sol (L680-702)
```text
        require(
            _validateOrder(
                callState,
                market,
                taker,
                ordersInfo.taker.digest,
                true,
                txn.takerLinkedSigner
            ),
            ERR_INVALID_TAKER
        );
        require(
            _validateOrder(
                callState,
                market,
                maker,
                ordersInfo.maker.digest,
                false,
                txn.makerLinkedSigner
            ),
            ERR_INVALID_MAKER
        );

```

**File:** core/contracts/OffchainExchange.sol (L757-759)
```text
        ordersInfo.taker.amountDelta -=
            ordersInfo.taker.amountDelta %
            market.sizeIncrement;
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

**File:** core/contracts/PerpEngine.sol (L33-45)
```text
    function addOrUpdateProduct(
        uint32 productId,
        int128 sizeIncrement,
        int128 minSize,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            QUOTE_PRODUCT_ID,
            sizeIncrement,
            minSize,
            riskStore
        );
```
