### Title
`DirectDepositV1`: Unsupported ERC20 Tokens Sent to the Contract Become Permanently Stuck - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1` (DDA) is a user-facing deposit relay contract. It accepts any ERC20 token transfer but only processes tokens registered in the `SpotEngine` via `creditDeposit()`. If a user sends an unsupported ERC20 token to the DDA, it is silently ignored by `creditDeposit()` and permanently locked in the contract. The only recovery path is `withdraw(token)`, which is `onlyOwner` and transfers the balance to the **owner**, not the original depositor.

---

### Finding Description

`DirectDepositV1.creditDeposit()` iterates exclusively over `spotEngine.getProductIds()` and only processes tokens whose addresses are returned by `spotEngine.getToken(productId)`: [1](#0-0) 

Any ERC20 token that is **not** registered in the `SpotEngine` will have a non-zero balance in the DDA after a user transfer, but `creditDeposit()` will never touch it — the loop only covers registered product IDs. There is no fallback, no event, and no revert to warn the user.

The only withdrawal path for stuck tokens is: [2](#0-1) 

This is `onlyOwner` and sends the full balance to `msg.sender` (the owner), **not** to the original depositor. There is no mapping of depositor → amount for unsupported tokens, and no user-callable recovery function exists.

The constructor comment for native token handling explicitly acknowledges the "stuck forever" risk for ETH, yet the same concern is not addressed for ERC20 tokens: [3](#0-2) 

---

### Impact Explanation

A user who sends an unsupported ERC20 token to a deployed `DirectDepositV1` contract loses those tokens permanently unless the owner intervenes. Even if the owner calls `withdraw(token)`, the tokens are transferred to the **owner's address**, not back to the original depositor. There is no on-chain record of who deposited the unsupported token or how much, so the owner has no trustless way to return funds to the correct party. The corrupted state is: the user's token balance is zero, the DDA's balance of the unsupported token is non-zero (or transferred to owner), and the user's protocol subaccount balance is unchanged — a net permanent loss of user funds.

---

### Likelihood Explanation

`DirectDepositV1` is a publicly deployed, user-facing contract. Users interact with it by sending tokens directly to its address and then calling `creditDeposit()`. Realistic triggers include:

- A user sends a token that was previously supported but has since been delisted from the `SpotEngine`
- A user mistakenly sends the wrong ERC20 token (e.g., a wrapped variant vs. the registered token)
- A user sends tokens to the DDA before the corresponding product is registered in the `SpotEngine`

No privileged access is required. Any unprivileged user can trigger this by sending an ERC20 token to the DDA contract address.

---

### Recommendation

Add a validation check in `creditDeposit()` or provide a user-accessible recovery function. The minimal fix mirrors the VaultBooster recommendation: track unsupported deposits with depositor address and amount, and provide a user-callable refund function. Alternatively, document clearly that only registered tokens should be sent, and add a user-callable `withdrawUnsupported(token, to)` that verifies the token is **not** a registered product before allowing the caller to reclaim their own deposit.

---

### Proof of Concept

1. A `DirectDepositV1` is deployed for `subaccount = alice_subaccount`, with `spotEngine` registering only `USDC` (productId=0).
2. Alice sends `1000e18` of `TOKEN_X` (not registered in `spotEngine`) directly to the DDA contract address.
3. Alice calls `creditDeposit()`. The loop iterates over `[0]` (USDC only), checks `TOKEN_X.balanceOf(DDA) == 0` (since she sent `TOKEN_X`, not USDC), and exits. `TOKEN_X` balance in DDA remains `1000e18`.
4. Alice has no function to call to recover `TOKEN_X` — there is no user-accessible withdrawal.
5. The owner calls `withdraw(TOKEN_X)`. `TOKEN_X` is transferred to `owner`, not to Alice.
6. Alice's `TOKEN_X` is permanently lost from her perspective, with no on-chain record of her deposit. [4](#0-3)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L53-59)
```text
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
```

**File:** core/contracts/DirectDepositV1.sol (L83-106)
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

    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
