### Title
`isHealthy()` Always Returns `true` — Post-Fill Health Check Permanently Bypassed in `OffchainExchange` — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.matchOrders()` enforces post-fill solvency via two `require(isHealthy(...))` calls. However, the `isHealthy()` function is a virtual stub that unconditionally returns `true` and is **never overridden** anywhere in the production codebase. The health guard is therefore a dead letter: any fill, regardless of how deeply it leaves a subaccount underwater, will succeed on-chain.

---

### Finding Description

`OffchainExchange.sol` defines `isHealthy()` as:

```solidity
function isHealthy(
    bytes32 /* subaccount */
) internal view virtual returns (bool) {
    return true;
}
``` [1](#0-0) 

After updating both sides' balances, `matchOrders()` calls:

```solidity
require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
``` [2](#0-1) 

A codebase-wide search for any override of `isHealthy` returns **zero results** outside of `OffchainExchange.sol` itself. The only concrete contract in scope is `OffchainExchange`, which is the production deployment target — no subclass exists that would supply a real implementation.

`Clearinghouse.getHealth()` — the function that actually computes maintenance/initial health across all spot and perp positions — is called in `withdrawCollateral`, `transferQuote`, `mintNlp`, `burnNlp`, and `forceRebalanceNlpPool`, but **never** in the order-matching path. [3](#0-2) 

---

### Impact Explanation

Every post-fill health check in `matchOrders` is a no-op. A subaccount with zero collateral can be matched into an arbitrarily large position. Because the perp engine records the position and the quote delta immediately via `_updateBalances`, the subaccount's on-chain state reflects a deeply negative health score, but the transaction succeeds. The protocol has no on-chain mechanism to prevent this at fill time, leaving it with unrecoverable bad debt. [4](#0-3) 

---

### Likelihood Explanation

The sequencer is the only entity that can submit `MatchOrders` transactions (the `onlyEndpoint` modifier gates `matchOrders`). A fully honest sequencer would not deliberately match undercollateralized orders. However:

- The on-chain invariant is supposed to be the last line of defense; its complete absence means any sequencer bug, misconfiguration, or compromise immediately translates to protocol insolvency with no on-chain backstop.
- The `onlyEndpoint` guard does not prevent the sequencer from submitting orders for accounts it has already accepted as valid off-chain but whose health has since deteriorated (e.g., due to price moves between sequencer validation and on-chain settlement). [5](#0-4) 

---

### Recommendation

Override `isHealthy()` in `OffchainExchange` (or a production subclass) to call `clearinghouse.getHealth()` and enforce the maintenance health invariant:

```solidity
function isHealthy(bytes32 subaccount) internal view override returns (bool) {
    return clearinghouse.getHealth(
        subaccount,
        IProductEngine.HealthType.MAINTENANCE
    ) >= 0;
}
```

This mirrors the pattern already used in `Clearinghouse.withdrawCollateral` and `Clearinghouse.burnNlp`. [6](#0-5) 

---

### Proof of Concept

1. Deploy the protocol on a local Hardhat fork.
2. Create a subaccount with zero collateral (no `depositCollateral` call).
3. Sign a large perp order (e.g., buy 1000 BTC-PERP at market price).
4. Have the sequencer submit a `MatchOrders` transaction pairing it against a well-collateralized maker.
5. After the transaction, call `clearinghouse.getHealth(subaccount, MAINTENANCE)`.
6. Observe the returned value is deeply negative (e.g., `-1000 * price * initialWeight`), yet the fill succeeded without revert.

The transaction succeeds because `isHealthy()` returns `true` unconditionally, and `clearinghouse.getHealth()` is never invoked in the fill path. [1](#0-0)

### Citations

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L631-634)
```text
    function matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
        external
        onlyEndpoint
    {
```

**File:** core/contracts/OffchainExchange.sol (L811-827)
```text
        _updateBalances(
            callState,
            market.quoteId,
            taker.order.sender,
            ordersInfo.taker.amountDelta,
            ordersInfo.taker.quoteDelta
        );
        _updateBalances(
            callState,
            market.quoteId,
            maker.order.sender,
            ordersInfo.maker.amountDelta,
            ordersInfo.maker.quoteDelta
        );

        require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
        require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
```

**File:** core/contracts/Clearinghouse.sol (L71-84)
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
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
