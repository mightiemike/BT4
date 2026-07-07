### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Drain Without USDC Payment — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` is a publicly callable function (no `onlyOwner`) that is intended to atomically swap usdcE held in a `DirectDepositV1` (DDA) contract for USDC. It calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` directly, without using the `safeTransferFrom` wrapper from `ERC20Helper`, and without checking the return value. If the `transferFrom` call returns `false` instead of reverting, execution continues, and the caller still receives the usdcE from the DDA — without having paid any USDC.

---

### Finding Description

The function `replaceUsdcEWithUsdc` in `ContractOwner.sol` performs three sequential operations:

1. Calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` — pulls USDC from the caller into the DDA.
2. Calls `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulls usdcE from the DDA into the `ContractOwner` contract.
3. Calls `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sends usdcE to the caller.

Step 1 uses a raw `transferFrom` call on the `IERC20Base` interface, not the `ERC20Helper.safeTransferFrom` wrapper used everywhere else in the codebase. The return value is silently discarded. If the USDC token returns `false` on failure (rather than reverting), steps 2 and 3 still execute, and the caller receives usdcE for free.

The function has no `onlyOwner` modifier — it is callable by any address on chain 57073. [1](#0-0) 

Compare with the safe pattern used everywhere else in the codebase: [2](#0-1) 

The `ERC20Helper.safeTransferFrom` wrapper checks `success && (data.length == 0 || abi.decode(data, (bool)))` and reverts on failure. The raw call at line 616 does neither. [3](#0-2) 

The `DirectDepositV1.withdraw` function is `onlyOwner`, and the owner of each DDA is the `ContractOwner` contract itself, so the call at step 2 succeeds unconditionally from `ContractOwner`'s context. [4](#0-3) 

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose DDA holds usdcE can receive the full usdcE balance without transferring any USDC, if the USDC token's `transferFrom` returns `false` rather than reverting. The usdcE is drained from the DDA (a user-owned deposit address) and transferred to the attacker. The corrupted asset delta is: attacker gains `balance` usdcE, DDA loses `balance` usdcE, no USDC is deposited.

---

### Likelihood Explanation

The function is permissionlessly callable by any address on chain 57073 (Ink). The exploitability depends on whether the specific USDC deployment at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink can return `false` without reverting. Many USDC deployments revert on failure, which would prevent exploitation. However, the code is structurally broken regardless: it deviates from the established `ERC20Helper.safeTransferFrom` pattern used throughout the rest of the codebase with no justification, and any token substitution or upgrade that introduces a non-reverting failure path would immediately enable the drain.

---

### Recommendation

Replace the raw `transferFrom` call with the `ERC20Helper.safeTransferFrom` wrapper already used throughout the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This ensures the call reverts if the transfer fails, preserving the atomicity of the swap.

---

### Proof of Concept

1. Identify a subaccount `S` whose DDA (`directDepositV1Address[S]`) holds a non-zero usdcE balance `B`.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(S)` on chain 57073 with zero USDC allowance (or with a USDC token that returns `false` on failure).
3. `IERC20Base(usdc).transferFrom(attacker, dda, B)` returns `false`; return value is not checked; execution continues.
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers `B` usdcE from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, B)` transfers `B` usdcE to the attacker.
6. Attacker has received `B` usdcE without paying any USDC. The DDA's usdcE balance is now zero. [1](#0-0)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
