### Title
One-Step Approval in `DirectDepositV1.creditDeposit()` Can Permanently Block Deposits for USDT-Like Tokens — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without first resetting the allowance to zero. For tokens like USDT that revert on a non-zero → non-zero approval, any residual allowance left from a prior call will permanently brick the deposit path for that token in the DDA contract.

---

### Finding Description

In `creditDeposit()`, for each supported spot product, the contract approves the endpoint for the full token balance and then calls `depositCollateralWithReferral`:

```solidity
token.approve(address(endpoint), balance);          // line 92
endpoint.depositCollateralWithReferral(
    subaccount,
    productId,
    uint128(balance),   // ← truncated to uint128
    "-1"
);
```

The approved amount is `balance` typed as `uint256`, but the deposit amount passed to the endpoint is `uint128(balance)`. If `balance > type(uint128).max`, the endpoint's `transferFrom` only consumes `uint128(balance)` tokens, leaving `balance - uint128(balance)` as a residual allowance on the endpoint spender. On the next invocation of `creditDeposit()`, the bare `approve()` call will revert for USDT-like tokens because their implementation forbids changing a non-zero allowance to another non-zero value without first clearing it.

By contrast, `ContractOwner.wrapVaultAsset()` correctly uses the two-step pattern:

```solidity
assetToken.approve(tokenAddr, 0);          // line 530
assetToken.approve(tokenAddr, assetBalance); // line 531
```

`creditDeposit()` has no access control — it is `external` and callable by any address.

---

### Impact Explanation

Once a residual allowance exists for a USDT-like token, every subsequent call to `creditDeposit()` reverts at the `approve()` line. The DDA contract holds user funds (tokens sent to it by users expecting them to be credited to their subaccount), and those funds become permanently undepositable through the normal flow. The only recovery path is the owner-gated `withdraw()` function, which requires admin intervention and breaks the permissionless deposit guarantee of the protocol.

---

### Likelihood Explanation

USDT is a widely used stablecoin and is a realistic candidate for a supported spot product. The uint256-vs-uint128 truncation trigger is deterministic: any token balance exceeding `type(uint128).max` (~3.4 × 10^38 in raw units, which for a 6-decimal token like USDT equals ~3.4 × 10^32 USDT) is an extreme edge case, but residual allowance can also arise if the endpoint's internal deposit logic reverts after the approval is set, or if the endpoint only partially consumes the allowance for any other protocol-level reason. The missing zero-reset is the root cause regardless of trigger path.

---

### Recommendation

Apply the same two-step approval pattern already used in `ContractOwner.wrapVaultAsset()`:

```solidity
// Before:
token.approve(address(endpoint), balance);

// After:
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

---

### Proof of Concept

1. A USDT-like token is listed as a supported spot product.
2. A user sends USDT to the DDA address.
3. Anyone calls `creditDeposit()`. The endpoint's `depositCollateralWithReferral` is called with `uint128(balance)` but the approval was for `balance` (uint256). If any residual allowance remains (e.g., due to partial consumption or a prior failed deposit), the allowance is non-zero.
4. On the next call to `creditDeposit()`, `token.approve(address(endpoint), balance)` reverts because USDT forbids non-zero → non-zero approvals.
5. All subsequent `creditDeposit()` calls for that token revert permanently. Funds accumulate in the DDA with no deposit path available to users.

**Vulnerable line:** [1](#0-0) 

**Correct two-step pattern already present elsewhere:** [2](#0-1)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L92-98)
```text
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
```

**File:** core/contracts/ContractOwner.sol (L530-531)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```
