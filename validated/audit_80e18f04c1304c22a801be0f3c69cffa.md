### Title
`ContractOwner` Cannot Receive Native ETH, Permanently Locking Native Assets in `DirectDepositV1` — (File: `core/contracts/ContractOwner.sol`)

---

### Summary
`ContractOwner` has no `receive()` or `fallback()` function and no `payable` functions. The `withdrawFromDirectDepositV1` function is designed to pull native ETH from a `DirectDepositV1` (DDA) contract back to the owner, but the intermediate ETH transfer to `ContractOwner` itself always reverts, making any native ETH that accumulates in a DDA permanently unrecoverable through the intended path.

---

### Finding Description
`DirectDepositV1.withdrawNative()` sends the contract's native ETH balance to its caller via a low-level call:

```solidity
// DirectDepositV1.sol L108-L111
function withdrawNative() external onlyOwner {
    uint256 balance = address(this).balance;
    (bool success, ) = msg.sender.call{value: balance}("");
    require(success, "Failed to transfer native token to owner");
}
```

The only caller of this function in the protocol is `ContractOwner.withdrawFromDirectDepositV1` when `token == address(0)`:

```solidity
// ContractOwner.sol L628-L636
if (token == address(0)) {
    uint256 preBalance = address(this).balance;
    DirectDepositV1(directDepositV1).withdrawNative();   // <-- ETH sent to ContractOwner
    uint256 postBalance = address(this).balance;
    require(postBalance > preBalance, "empty");
    (bool success, ) = msg.sender.call{value: postBalance - preBalance}("");
    require(success, "xfer");
}
```

`ContractOwner` is an upgradeable contract that inherits only `EIP712Upgradeable` and `OwnableUpgradeable`. It declares no `receive()` function, no `fallback()` function, and none of its functions are marked `payable`. When `withdrawNative()` executes `msg.sender.call{value: balance}("")` with `msg.sender == address(ContractOwner)`, the EVM rejects the ETH transfer because the target has no payable entry point. The `require(success, ...)` in `withdrawNative()` then reverts the entire transaction. [1](#0-0) [2](#0-1) 

---

### Impact Explanation
**High.** Any native ETH that ends up in a `DirectDepositV1` contract — whether from a failed wrap in the constructor, a direct send that bypasses the `receive()` wrapper, or any other edge-case accumulation — is permanently unrecoverable. The `withdrawFromDirectDepositV1(subaccount, address(0))` call will always revert at the `withdrawNative()` step, with no alternative recovery path in the protocol. The ETH is locked in the DDA forever. [3](#0-2) 

---

### Likelihood Explanation
**High.** Native ETH can reach a DDA through multiple realistic paths: the DDA constructor explicitly handles a non-zero `address(this).balance` at deploy time and attempts to wrap it, but if that wrap call fails it emits `NativeTokenTransferFailed` and leaves the ETH in the contract. Additionally, the `receive()` function in `DirectDepositV1` wraps incoming ETH, but any revert in the wrap call would leave residual ETH. Native assets are a primary collateral type on EVM chains, making this a high-probability scenario. [4](#0-3) 

---

### Recommendation
Add a `receive() external payable {}` function to `ContractOwner` so it can accept the intermediate ETH transfer from `DirectDepositV1.withdrawNative()` before forwarding it to `msg.sender`. [5](#0-4) 

---

### Proof of Concept
1. A `DirectDepositV1` DDA is deployed for a subaccount. During construction, if `address(this).balance != 0` and the wrap call to `wrappedNative` fails, native ETH remains in the DDA.
2. The multisig owner calls `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))`.
3. Execution reaches `DirectDepositV1(directDepositV1).withdrawNative()`.
4. Inside `withdrawNative()`, `msg.sender.call{value: balance}("")` is executed where `msg.sender` is `ContractOwner`.
5. `ContractOwner` has no `receive()` or `fallback()` and no `payable` function — the EVM rejects the ETH transfer, returning `success = false`.
6. `require(success, "Failed to transfer native token to owner")` reverts the transaction.
7. The native ETH remains permanently locked in the DDA with no recovery path. [1](#0-0) [6](#0-5)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L52-67)
```text
        uint256 balance = address(this).balance;
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
        }
        emit DirectDepositV1Created(version(), subaccount, address(this));
    }

    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/DirectDepositV1.sol (L108-112)
```text
    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```

**File:** core/contracts/ContractOwner.sol (L21-21)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
```

**File:** core/contracts/ContractOwner.sol (L622-647)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```
