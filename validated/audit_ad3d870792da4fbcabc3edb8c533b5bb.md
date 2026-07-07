### Title
`burnNlp` Uses Weaker `MAINTENANCE` Health Check Instead of `INITIAL`, Allowing Under-Collateralized State After NLP Burn — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The `burnNlp` function in `Clearinghouse.sol` enforces a `MAINTENANCE` health check after reducing a user's NLP balance and crediting quote tokens. Every other collateral-reducing operation in the protocol (`withdrawCollateral`, `transferQuote`, `mintNlp`) enforces an `INITIAL` health check. Because the burn fee can cause net health to decrease, a user can burn NLP and end up with initial health < 0 but maintenance health >= 0 — violating the protocol's margin invariant and leaving the account under-collateralized without being liquidatable.

---

### Finding Description

In `burnNlp`, after the NLP balance is reduced and quote is credited minus a burn fee, the post-operation health check is:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

The comment immediately above this check acknowledges the problem:

> "Burning NLP can decrease health if the burn fee exceeds the health improvement from the withdrawal." [2](#0-1) 

The burn fee is computed as `MathHelper.max(ONE, quoteAmount / 1000)` and deducted from the quote credited to the user. When this fee exceeds the health gain from receiving quote, the user's health decreases. The `MAINTENANCE` check only catches the liquidation floor — it does not enforce the initial margin requirement.

Compare with every other collateral-reducing path:

- `withdrawCollateral` uses `IProductEngine.HealthType.INITIAL`: [3](#0-2) 

- `transferQuote` calls `_isAboveInitial` (which checks `INITIAL` health): [4](#0-3) 

- `mintNlp` (the inverse operation) checks `INITIAL` health: [5](#0-4) 

- `_isAboveInitial` itself confirms the intended standard: [6](#0-5) 

The `MAINTENANCE` threshold is the liquidation floor — it is strictly weaker than `INITIAL`. Using it here is the direct analog of the external report's inverted comparison: the wrong bound is applied in the guard, allowing operations that should be blocked.

---

### Impact Explanation

A user can burn NLP and end up with `INITIAL` health < 0 while `MAINTENANCE` health >= 0. This means:

1. The user has extracted more collateral (quote tokens) than the initial margin rules permit.
2. The account is under-collateralized relative to the initial margin requirement but is not yet liquidatable.
3. The protocol's invariant — that any collateral-reducing action must leave the account above initial margin — is broken.
4. The corrupted state persists: the account sits in the "under initial, above maintenance" zone, which is a state the protocol explicitly prevents for all other operations.

The asset delta is concrete: the user receives quote tokens from the NLP burn while their initial health goes negative, meaning they hold more risk than their collateral supports.

---

### Likelihood Explanation

Medium. The trigger requires a user whose initial health is close to zero (leveraged position) and who holds NLP tokens. The burn fee (`max(1e18, quoteAmount / 1000)`) is non-trivial and can exceed the health improvement from receiving quote when the user is near the initial margin boundary. This is a realistic scenario for any active trader who also participates in the NLP pool.

The entry path is fully unprivileged: any user can call `burnNlp` through the `Endpoint` with a valid signed transaction.

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with `INITIAL`, consistent with all other collateral-reducing operations:

```solidity
// Before (incorrect):
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);

// After (correct):
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

---

### Proof of Concept

1. User opens a leveraged perp position; their `INITIAL` health is just above 0 (e.g., `+1e15`).
2. User holds NLP tokens worth `quoteAmount = 1e18` (1 USDC in 18-decimal form).
3. Burn fee = `max(1e18, 1e18 / 1000)` = `1e18` (the `ONE` floor dominates for small burns).
4. `quoteAmount` credited = `max(0, 1e18 - 1e18)` = 0. The user receives nothing but their NLP is burned.
5. Health decreases by the value of the burned NLP minus zero quote received.
6. `INITIAL` health drops below 0; `MAINTENANCE` health remains >= 0.
7. The `MAINTENANCE` check at line 527 passes. Transaction succeeds.
8. User's account is now under initial margin — a state rejected by `withdrawCollateral`, `transferQuote`, and `mintNlp` — but accepted here due to the wrong health type in the comparison.

### Citations

**File:** core/contracts/Clearinghouse.sol (L249-249)
```text
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L523-529)
```text
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L639-642)
```text
    function _isAboveInitial(bytes32 subaccount) internal returns (bool) {
        // Weighted initial health with limit orders < 0
        return getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
    }
```
