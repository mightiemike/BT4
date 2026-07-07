### Title
Non-Standard ERC20 Token `approve()` Without Safe Wrapper Permanently Blocks `creditDeposit()` and Locks Collateral in DirectDepositV1 — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve()` directly via the `IIERC20Base` interface, which declares `approve` as `returns (bool)`. For non-standard ERC20 tokens that do not return a value from `approve()` (e.g., USDT on mainnet), Solidity's ABI decoder reverts when it attempts to decode the empty return data as `bool`. This causes `creditDeposit()` to revert for any DDA holding such a token, permanently blocking the deposit flow and locking user collateral in the DDA contract.

---

### Finding Description

`ERC20Helper` provides safe wrappers for `transfer` and `transferFrom` using low-level `.call()` that tolerates empty return data: [1](#0-0) 

However, no equivalent safe wrapper exists for `approve`. In `DirectDepositV1.creditDeposit()`, the approval is issued via a direct high-level interface call: [2](#0-1) 

The `IIERC20Base` interface declares `approve` as `returns (bool)`: [3](#0-2) 

When Solidity makes a high-level call to an interface function declared with a return type, it unconditionally runs the ABI decoder on the return data. If the token returns zero bytes (as USDT does), the decoder reverts with an ABI decoding error. The `safeTransfer` helper in the same file correctly handles this pattern for `transfer`, but `approve` is left unprotected: [4](#0-3) 

`creditDeposit()` is `external` with no access modifier, so any caller can invoke it: [5](#0-4) 

---

### Impact Explanation

If a non-standard ERC20 token (one whose `approve()` returns no value) is registered as a spot product, any DDA that receives that token will have its `creditDeposit()` permanently broken. The loop iterates all product IDs and calls `approve` for each token with a non-zero balance, so a single non-standard token in the DDA causes the entire function to revert. Tokens sent to the DDA by users expecting automatic deposit into the protocol are stuck. The only recovery path is the `onlyOwner` `withdraw()` function, which requires privileged intervention and does not complete the intended deposit. [6](#0-5) 

---

### Likelihood Explanation

Medium. The Nado protocol is designed to support multiple spot collateral tokens. If any listed token is non-standard with respect to `approve()` return values (USDT being the canonical example), the impact is immediate and affects every DDA holding that token. The entry path requires no privilege — any user or contract can call `creditDeposit()`.

---

### Recommendation

Replace the direct `token.approve(...)` call with a low-level safe wrapper analogous to the existing `safeTransfer` pattern already present in `DirectDepositV1`:

```solidity
function safeApprove(IIERC20Base self, address spender, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IIERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        "Approve failed"
    );
}
```

Then replace line 92 in `creditDeposit()` with `safeApprove(token, address(endpoint), balance)`. The same pattern should be applied to the direct `approve` calls in `ContractOwner.depositInsurance()` and `ContractOwner.wrapVaultAsset()`. [7](#0-6) [8](#0-7) 

---

### Proof of Concept

1. A non-standard ERC20 token `T` (e.g., USDT-style, `approve()` returns no value) is registered as a spot product in the SpotEngine.
2. A user sends `T` tokens directly to a `DirectDepositV1` DDA address.
3. Any caller invokes `DirectDepositV1.creditDeposit()`.
4. The loop reaches product `T`, finds `balance > 0`, and executes `token.approve(address(endpoint), balance)` at line 92.
5. Solidity's ABI decoder attempts to decode the empty return bytes as `bool` and reverts.
6. The entire `creditDeposit()` call reverts; no tokens are deposited.
7. The user's tokens remain locked in the DDA. Recovery requires the owner to call `withdraw()` manually, and the deposit into the protocol never completes. [5](#0-4)

### Citations

**File:** core/contracts/libraries/ERC20Helper.sol (L14-20)
```text
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
```

**File:** core/contracts/DirectDepositV1.sol (L11-11)
```text
    function approve(address spender, uint256 amount) external returns (bool);
```

**File:** core/contracts/DirectDepositV1.sol (L69-81)
```text
    function safeTransfer(
        IIERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IIERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            "Transfer failed"
        );
    }
```

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/ContractOwner.sol (L254-254)
```text
        quoteToken.approve(address(endpoint), uint256(amount));
```

**File:** core/contracts/ContractOwner.sol (L530-531)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```
