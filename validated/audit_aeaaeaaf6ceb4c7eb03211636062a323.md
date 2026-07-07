### Title
Direct `approve()` Call in `creditDeposit()` Reverts for Non-Bool-Returning Tokens, Permanently Blocking Deposits — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary
`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` directly through the `IIERC20Base` interface, which declares `approve` as returning `bool`. For ERC-20 tokens such as USDT whose `approve()` returns no value, the Solidity ABI decoder reverts when attempting to decode the missing return data. Because `creditDeposit()` has no access control, any user can trigger it, and any supported spot token that exhibits this behavior will cause the entire deposit flow for that `DirectDepositV1` instance to permanently revert, locking deposited funds.

---

### Finding Description

`DirectDepositV1.sol` defines a local interface `IIERC20Base` with `approve` declared as returning `bool`:

```solidity
// DirectDepositV1.sol L11
function approve(address spender, uint256 amount) external returns (bool);
```

In `creditDeposit()`, for every spot product whose token balance is non-zero, the contract calls:

```solidity
// DirectDepositV1.sol L92
token.approve(address(endpoint), balance);
```

This is a high-level interface call. Solidity will ABI-decode the return data as `bool`. USDT (and other non-standard ERC-20s) return no data from `approve()`. The ABI decoder receives zero bytes and reverts.

The contract already recognises this class of problem for `transfer`: the `safeTransfer` helper at lines 69–81 uses a low-level `call` and explicitly handles the `data.length == 0` case:

```solidity
// DirectDepositV1.sol L74-L79
(bool success, bytes memory data) = address(self).call(
    abi.encodeWithSelector(IIERC20Base.transfer.selector, to, amount)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    "Transfer failed"
);
```

No equivalent safe wrapper exists for `approve` anywhere in the codebase — confirmed by the absence of `safeApprove`, `safeIncreaseAllowance`, or `safeDecreaseAllowance` across all Solidity files. `ERC20Helper` (`core/contracts/libraries/ERC20Helper.sol`) only provides `safeTransfer` and `safeTransferFrom`.

---

### Impact Explanation

If a supported spot product token does not return a `bool` from `approve()` (e.g., USDT), every call to `creditDeposit()` for any `DirectDepositV1` instance holding that token will revert unconditionally. The deposited tokens are stranded in the `DirectDepositV1` contract and cannot be credited to the user's subaccount through the normal deposit flow. Recovery requires the multisig owner to call `ContractOwner.withdrawFromDirectDepositV1()`, which is an out-of-band privileged action. The user's deposit is effectively blocked until manual intervention.

---

### Likelihood Explanation

`creditDeposit()` is `external` with no access modifier — any address can call it. The `DirectDepositV1` deposit flow is a documented, user-facing entry point (`ContractOwner.creditDepositV1()` is also callable by anyone). USDT is one of the most widely used stablecoins and a natural candidate for a spot collateral product. If it is listed as a spot product, every user attempting to deposit USDT via the direct deposit flow will be affected.

---

### Recommendation

Replace the direct `approve()` call with a low-level safe wrapper analogous to the existing `safeTransfer` helper already present in `DirectDepositV1.sol`:

```solidity
function safeApprove(
    IIERC20Base self,
    address spender,
    uint256 amount
) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IIERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        "Approve failed"
    );
}
```

Then replace line 92:

```solidity
// Before:
token.approve(address(endpoint), balance);

// After:
safeApprove(token, address(endpoint), balance);
```

Alternatively, extend `ERC20Helper` with a `safeApprove` function and apply it consistently across the codebase, including the bare `approve` calls in `ContractOwner.wrapVaultAsset()` (lines 530–531) and `ContractOwner.depositInsurance()` (line 254).

---

### Proof of Concept

1. A spot product is listed with USDT as its token (USDT does not return a `bool` from `approve()`).
2. A user sends USDT to their `DirectDepositV1` address.
3. Anyone calls `creditDeposit()` on that `DirectDepositV1` instance.
4. Execution reaches line 92: `token.approve(address(endpoint), balance)`.
5. The EVM executes USDT's `approve()`, which returns no data.
6. Solidity's ABI decoder attempts to decode a `bool` from zero bytes and reverts.
7. The entire `creditDeposit()` call reverts; no deposit is recorded; the USDT remains stranded in the `DirectDepositV1` contract.
8. Every subsequent call to `creditDeposit()` for this instance reverts identically. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L6-12)
```text
interface IIERC20Base {
    function transfer(address to, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function approve(address spender, uint256 amount) external returns (bool);
}
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

**File:** core/contracts/libraries/ERC20Helper.sol (L8-42)
```text
library ERC20Helper {
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }

    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```
