The code path is clear. Let me confirm the exact control flow and the missing guard.

### Title
Missing Liquidator Solvency Check in Finalization Path — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`_finalizeSubaccount` settles all positive perp PnL against the liquidator's QUOTE balance in a loop with no intermediate or post-loop health check. When finalization succeeds, `liquidateSubaccountImpl` returns immediately at line 626 — the only liquidator health guard in the contract (`ERR_SUBACCT_HEALTH`) lives exclusively inside `_handleLiquidationPayment`, which is never reached on the finalization path.

---

### Finding Description

`_settlePnlAgainstLiquidator` unconditionally decrements `txn.sender`'s QUOTE balance by `pnl` on every call: [1](#0-0) 

The positive-PnL loop in `_finalizeSubaccount` calls this N times — once per perp product with `vQuoteBalance > 0` — with no guard between iterations: [2](#0-1) 

After the loop, `_finalizeSubaccount` returns `true`. Back in `liquidateSubaccountImpl`, the only branch taken is: [3](#0-2) 

Execution returns at line 626. The liquidator health check: [4](#0-3) 

is inside `_handleLiquidationPayment`, which is only reached when `_finalizeSubaccount` returns `false` (line 646). It is **never executed** on the finalization path.

---

### Impact Explanation

A liquidatee with N perp products each carrying a small positive `vQuoteBalance` (total sum S) can be finalized by a liquidator whose QUOTE balance is less than S. After the Kth settlement (K < N), the liquidator's QUOTE balance goes negative. The transaction does not revert. The protocol now has two undercollateralized accounts: the liquidatee (being finalized) and the liquidator (newly insolvent). The second insolvency is undetected and unrecorded at the time it occurs, and the liquidator's account can subsequently be used to further interact with the protocol in an undercollateralized state.

---

### Likelihood Explanation

The finalization path is triggered by any caller setting `productId = type(uint32).max`. The precondition — a liquidatee with zero perp position amounts but nonzero positive `vQuoteBalance` across multiple products — is a normal end-state of the standard liquidation sequence (perp positions are closed first, leaving residual PnL). No special privileges are required. Any liquidator with insufficient QUOTE to cover the total positive PnL of the liquidatee will trigger this silently.

---

### Recommendation

Add a liquidator health check immediately after `_finalizeSubaccount` returns `true`, mirroring the existing guard in `_handleLiquidationPayment`:

```solidity
if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
    require(
        txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
        ERR_SUBACCT_HEALTH
    );
    if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
        IOffchainExchange(
            IEndpoint(getEndpoint()).getOffchainExchange()
        ).tryCloseIsolatedSubaccount(txn.liquidatee);
    }
    return;
}
``` [3](#0-2) 

---

### Proof of Concept

**Setup:**
- Liquidatee has 3 perp products, each with `amount = 0` and `vQuoteBalance = 200e18` (total S = 600e18 USDC).
- Liquidator has QUOTE balance = 400e18 USDC and no other assets (initial health ≈ 400e18).

**Execution:**
1. Liquidator calls `liquidateSubaccountImpl` with `productId = type(uint32).max`.
2. `isUnderMaintenance(liquidatee)` passes (liquidatee is under maintenance).
3. `_finalizeSubaccount` enters the positive-PnL loop.
4. Iteration 1: `_settlePnlAgainstLiquidator` → liquidator QUOTE = 400 − 200 = 200.
5. Iteration 2: `_settlePnlAgainstLiquidator` → liquidator QUOTE = 200 − 200 = 0.
6. Iteration 3: `_settlePnlAgainstLiquidator` → liquidator QUOTE = 0 − 200 = **−200** (insolvent).
7. `_finalizeSubaccount` returns `true`.
8. `liquidateSubaccountImpl` returns at line 626. No health check fires.
9. Assert: `spotEngine.getBalance(QUOTE_PRODUCT_ID, txn.sender).amount == -200e18` — negative, no revert.

**Invariant broken:** `require(txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender))` is never evaluated on this path, violating the protocol's universal post-liquidation solvency guarantee for the liquidator. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L259-270)
```text
    function _settlePnlAgainstLiquidator(
        IEndpoint.LiquidateSubaccount calldata txn,
        uint32 perpId,
        int128 pnl,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal {
        perpEngine.updateBalance(perpId, txn.liquidatee, 0, -pnl);
        perpEngine.updateBalance(perpId, txn.sender, 0, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl);
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L322-338)
```text
        // settle all positive pnl
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance > 0) {
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    balance.vQuoteBalance,
                    spotEngine,
                    perpEngine
                );
            }
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L572-577)
```text
        // it's ok to let initial health become 0
        require(!isAboveInitial(txn.liquidatee), ERR_LIQUIDATED_TOO_MUCH);
        require(
            txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L620-627)
```text
        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```
