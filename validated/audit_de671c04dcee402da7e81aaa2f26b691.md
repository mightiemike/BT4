### Title
Unchecked `transferFrom` Return Value Enables USDC.e Drain from Direct Deposit Accounts — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `IERC20Base(usdc).transferFrom(...)` call whose return value is never checked. If the USDC token returns `false` on failure (valid ERC20 behavior) rather than reverting, the function silently continues, transfers USDC.e out of the target Direct Deposit Account (DDA) to `ContractOwner`, and then forwards it to the caller — without the caller ever paying USDC.

---

### Finding Description

`ContractOwner.replaceUsdcEWithUsdc` is an externally callable function (no `onlyOwner` or `onlyDeployer` modifier) that is gated only by `block.chainid == 57073`. [1](#0-0) 

The function's intended flow is:
1. Pull `balance` of USDC from `msg.sender` into the DDA via `transferFrom`.
2. Withdraw USDC.e from the DDA to `ContractOwner` via `DirectDepositV1.withdraw`.
3. Forward the USDC.e from `ContractOwner` to `msg.sender` via `safeTransfer`.

The critical defect is at line 616:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

This is a **raw** call whose return value is silently discarded. The contract already imports and activates `ERC20Helper` via `using ERC20Helper for IERC20Base`: [2](#0-1) 

`ERC20Helper.safeTransferFrom` correctly handles both non-standard tokens and failed transfers: [3](#0-2) 

The very next line in the same function uses the safe variant for the outgoing transfer: [4](#0-3) 

This inconsistency means step 1 can silently fail while steps 2 and 3 execute successfully.

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc(subaccount)` for any DDA that holds USDC.e, without holding or approving USDC, will:

- Pay **zero USDC** (the `transferFrom` returns `false`, no revert).
- Receive the **full USDC.e balance** of the targeted DDA.

The corrupted asset delta is: USDC.e balance of `directDepositV1Address[subaccount]` transferred to the attacker at zero cost. Every DDA on chain 57073 that holds USDC.e is at risk.

---

### Likelihood Explanation

The function is callable by any address on chain 57073 with no further access control. The exploitability depends on whether the hardcoded USDC contract at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed `transferFrom` or reverts. The ERC20 standard permits returning `false`; many tokens (including older USDC deployments and USDT) do exactly this. If the deployed USDC on Ink (chain 57073) follows this pattern, the attack is immediately executable by any unprivileged caller. Even if the current deployment reverts, the code is structurally broken and a future token migration or upgrade could make it exploitable without any code change on Nado's side.

---

### Recommendation

Replace the raw `transferFrom` call with the safe wrapper already available in scope:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` is already imported and activated via `using ERC20Helper for IERC20Base` in `ContractOwner`. This change makes the call consistent with every other token transfer in the contract and ensures a failed transfer reverts the entire transaction.

---

### Proof of Concept

1. A DDA exists for some `subaccount` on chain 57073 and holds `N` USDC.e.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC balance and zero USDC approval.
3. `IERC20Base(usdc).transferFrom(attacker, dda, N)` returns `false` (no revert).
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers `N` USDC.e from DDA → `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` transfers `N` USDC.e from `ContractOwner` → attacker.
6. Attacker receives `N` USDC.e having paid nothing. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

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

**File:** core/contracts/libraries/ERC20Helper.sol (L23-41)
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
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
