### Title
`DirectDepositV1.creditDeposit()` Permanently Blocked by Any Zero-Token Product, Locking User Deposits — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` iterates over every spot product ID returned by `spotEngine.getProductIds()` and hard-reverts with `require(tokenAddr != address(0), "Invalid productId.")` if any single product lacks a configured token. Because `NLP_PRODUCT_ID` is present in the spot engine's `productIds` array (evidenced by the explicit `continue` guard in `SpotEngineState.updateStates()`) yet may carry no token address in its `Config`, `creditDeposit()` can be permanently and unconditionally blocked. Any user who has already sent tokens to the DDA contract address cannot have those tokens credited to their subaccount.

---

### Finding Description

`DirectDepositV1.creditDeposit()` is the sole mechanism by which tokens sent directly to a DDA contract address are forwarded to the protocol:

```solidity
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");   // ← hard revert
        IIERC20Base token = IIERC20Base(tokenAddr);
        uint256 balance = token.balanceOf(address(this));
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
        }
    }
}
``` [1](#0-0) 

The loop has no `continue` path for products that lack a token. A single product with `tokenAddr == address(0)` causes the entire call to revert, blocking credit for every other token held by the contract.

`SpotEngineState.updateStates()` contains an explicit guard:

```solidity
if (productId == NLP_PRODUCT_ID) {
    continue;
}
``` [2](#0-1) 

This guard exists precisely because `NLP_PRODUCT_ID` is present in the `productIds` array but is not a normal tradeable product. Its `Config.token` field is therefore `address(0)`. `creditDeposit()` contains no equivalent guard, so when the loop reaches `NLP_PRODUCT_ID`, `spotEngine.getToken(NLP_PRODUCT_ID)` returns `address(0)` and the `require` reverts unconditionally.

The protocol's own comment in `Endpoint.depositCollateralWithReferral` acknowledges the exact class of risk:

```solidity
// we cannot revert here, otherwise direct deposit could be blocked when there are
// multiple assets awaiting credit but one of them is below the minimum deposit amount.
// we can just skip the deposit and continue with the next asset.
return;
``` [3](#0-2) 

The mitigation applied there (`return` instead of `revert`) was scoped only to the minimum-deposit-amount check. The zero-token-address case in `creditDeposit()` was left as a hard `require`, creating the same class of blocking bug the comment was written to prevent.

---

### Impact Explanation

Any user who sends a supported ERC-20 token to a DDA contract address and then calls `creditDeposit()` (or relies on a keeper to call it) will find the call permanently reverting. Their tokens accumulate in the DDA contract with no automatic path to credit. Recovery requires the DDA owner to call `withdraw()`, which is an admin-gated function — the user has no self-service remedy. The DDA deposit pathway is effectively non-functional for the entire lifetime of the deployment.

---

### Likelihood Explanation

`NLP_PRODUCT_ID` is unconditionally present in the spot engine's `productIds` array (the `updateStates` guard confirms this). Its `Config.token` is `address(0)` because it is not a transferable ERC-20 collateral product. Therefore the blocking condition is not a hypothetical edge case — it is the default state of every deployed DDA contract. Any call to `creditDeposit()` will revert on the first iteration that reaches `NLP_PRODUCT_ID`.

---

### Recommendation

Replace the hard `require` with a `continue` to match the intent already expressed in `Endpoint.depositCollateralWithReferral`:

```solidity
address tokenAddr = spotEngine.getToken(productId);
if (tokenAddr == address(0)) continue;   // skip products with no token
```

This mirrors the skip already applied in `SpotEngineState.updateStates()` and the `return`-instead-of-revert pattern in `Endpoint.depositCollateralWithReferral`.

---

### Proof of Concept

1. `NLP_PRODUCT_ID` is registered in the spot engine's `productIds` array; `SpotEngineState.updateStates()` must explicitly skip it, confirming its presence.
2. `spotEngine.getToken(NLP_PRODUCT_ID)` returns `address(0)` because no ERC-20 token is configured for that product slot.
3. Alice sends 1000 USDC to a deployed `DirectDepositV1` contract address.
4. Alice (or any keeper) calls `creditDeposit()`.
5. The loop reaches `NLP_PRODUCT_ID`; `require(tokenAddr != address(0), "Invalid productId.")` reverts.
6. Alice's 1000 USDC remains locked in the DDA contract. She cannot credit the deposit herself; only the DDA owner can recover the funds via `withdraw()`.

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/SpotEngineState.sol (L268-272)
```text
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            if (productId == NLP_PRODUCT_ID) {
                continue;
            }
```

**File:** core/contracts/Endpoint.sol (L137-142)
```text
        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }
```
