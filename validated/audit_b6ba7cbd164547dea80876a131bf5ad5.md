### Title
Unsafe Direct `approve()` Call in `DirectDepositV1.creditDeposit()` Blocks Deposits for Non-Standard ERC20 Tokens - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve()` directly through the `IIERC20Base` interface. For ERC20 tokens that do not return a `bool` from `approve` (a known class of non-standard tokens), the ABI decoder will revert when it attempts to decode the missing return value. This permanently blocks the deposit-credit flow for any spot product whose collateral token exhibits this behavior.

---

### Finding Description

`ERC20Helper` — the project's own safe-transfer library — implements `safeTransfer` and `safeTransferFrom` using low-level `.call()` to tolerate tokens that omit the `bool` return value. However, **no `safeApprove` equivalent exists**, and every `approve` call in the codebase is a direct interface call.

The critical path is in `DirectDepositV1.creditDeposit()`:

```solidity
token.approve(address(endpoint), balance);   // line 92, DirectDepositV1.sol
```

`IIERC20Base.approve` is declared as `returns (bool)`. When the ABI decoder tries to decode the return data from a token that returns nothing, the call reverts. This is the same root cause as the reference report.

The entry point is `ContractOwner.creditDepositV1()`, which carries **no access modifier** — any unprivileged caller can invoke it:

```solidity
function creditDepositV1(bytes32 subaccount) external {   // line 502, ContractOwner.sol
    ...
    DirectDepositV1(directDepositV1).creditDeposit();
}
```

Two additional unguarded `approve` calls exist in `ContractOwner.wrapVaultAsset()` (also no access modifier):

```solidity
assetToken.approve(tokenAddr, 0);            // line 530
assetToken.approve(tokenAddr, assetBalance); // line 531
```

---

### Impact Explanation

If any spot product is configured with a non-standard ERC20 collateral token that omits the `bool` return from `approve`, every call to `creditDepositV1` for any subaccount will revert at the `approve` step. Funds already transferred into the `DirectDepositV1` escrow address become permanently stranded — they cannot be credited to the subaccount and cannot be recovered through the normal deposit flow. The `wrapVaultAsset` path is similarly bricked for vault-wrapped assets.

---

### Likelihood Explanation

The Nado protocol is designed to support multiple spot product tokens. The set of supported tokens is configurable and can expand over time. Tokens such as USDT (on some chains), BNB, and others are known to omit the `bool` return. If any such token is ever listed as a spot product, the `creditDeposit` path fails for all subaccounts holding that token in their DDA. The entry point (`creditDepositV1`) is publicly callable with no privilege requirement, so any user or keeper triggering it will encounter the revert.

---

### Recommendation

Add a `safeApprove` function to `ERC20Helper` mirroring the existing `safeTransfer` pattern, and replace all direct `.approve()` calls with it:

```solidity
function safeApprove(
    IERC20Base self,
    address spender,
    uint256 amount
) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
```

Apply this to:
- `DirectDepositV1.sol` line 92
- `ContractOwner.sol` lines 254, 530, 531

---

### Proof of Concept

1. A spot product is listed with a token that does not return `bool` from `approve` (e.g., a USDT-like token on a supported chain).
2. A user sends that token to their `DirectDepositV1` escrow address.
3. Anyone calls `ContractOwner.creditDepositV1(subaccount)`.
4. Execution reaches `DirectDepositV1.creditDeposit()` → `token.approve(address(endpoint), balance)`.
5. The token returns no data; the ABI decoder expects a `bool` and reverts.
6. The user's funds are stuck in the DDA with no recovery path through the normal deposit flow.

**Root cause:** [1](#0-0) 

**Unguarded public entry point:** [2](#0-1) 

**Safe wrapper exists for transfer but not approve:** [3](#0-2) 

**Additional unguarded approve calls in `wrapVaultAsset`:** [4](#0-3)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L91-93)
```text
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
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
```
