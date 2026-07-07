### Title
`burnNlp` Uses Weaker `MAINTENANCE` Health Check Instead of `INITIAL`, Allowing Collateral Extraction from Undercollateralized Subaccounts — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`burnNlp` enforces only a `MAINTENANCE` health check after burning NLP tokens, while the symmetric entry operation `mintNlp` and the analogous exit operation `withdrawCollateral` both enforce the stricter `INITIAL` health check. This asymmetry allows a subaccount whose `INITIAL` health has gone negative (but whose `MAINTENANCE` health remains non-negative) to burn NLP and receive quote tokens — effectively extracting collateral value from an account that the protocol's own invariants should have locked.

---

### Finding Description

In `Clearinghouse.sol`, three value-extraction operations apply different health thresholds:

**`mintNlp` (entry) — checks `INITIAL`:** [1](#0-0) 

**`withdrawCollateral` (exit) — checks `

### Citations

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```
