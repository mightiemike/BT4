### Title
Unchecked `transferFrom` Return Value Enables USDCe Drain from DirectDepositV1 Accounts - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` without checking its return value. If the USDC token returns `false` instead of reverting (e.g., on insufficient allowance), the function silently skips the inbound USDC transfer but continues to pull USDCe out of the target `DirectDepositV1` account and send it to the caller — effectively allowing any unprivileged caller to drain USDCe from any DDA for free.

---

### Finding Description

`replaceUsdcEWithUsdc` is a public, permissionless function (restricted only to `block.chainid == 57073`) intended to swap USDCe held in a `DirectDepositV1` (DDA) contract for native USDC. The swap logic is:

1. Read the USDCe balance of the DDA.
2. Pull `balance` USDC **from the caller** into the DDA via `transferFrom`.
3. Pull USDCe **out of the DDA** to `ContractOwner` via `DirectDepositV1.withdraw`.
4. Forward the USDCe to the caller via `safeTransfer`.

Step 2 uses a raw `transferFrom` call whose boolean return value is never checked:

```solidity
// ContractOwner.sol line 616
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);  // ← return value ignored
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));        // USDCe → ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                  // USDCe → caller
```

Steps 3 and 4 use the protocol's own `safeTransfer`/`safeTransferFrom` wrappers from `ERC20Helper`, which do check return values. Step 2 does not. If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed transfer (e.g., caller has zero allowance or zero balance), execution continues uninterrupted, and the caller receives USDCe without having deposited any USDC. [1](#0-0) 

The rest of the codebase consistently uses `ERC20Helper.safeTransferFrom`, which wraps the call and requires `success && (data.length == 0 || abi.decode(data, (bool)))`: [2](#0-1) 

This one site is the sole deviation from that pattern.

---

### Impact Explanation

An attacker can drain the entire USDCe balance of any DDA on Ink (chainid 57073) without providing USDC. The corrupted asset delta is:

- **DDA**: loses its full USDCe balance (transferred to `ContractOwner` then to attacker).
- **Attacker**: receives USDCe without paying USDC.
- **Subaccount owner**: their DDA is emptied; any pending `creditDeposit` call for that subaccount will find zero USDCe balance and credit nothing.

This is a direct, concrete collateral theft from user-owned DDA contracts. [3](#0-2) 

---

### Likelihood Explanation

- The function is callable by **any address** on Ink (chainid 57073) — no `onlyOwner` or similar guard.
- The only preconditions are: a DDA exists for some `subaccount` (publicly enumerable via `directDepositV1Address`) and it holds a nonzero USDCe balance.
- The attacker needs zero USDC allowance set for `ContractOwner` — the default state for any address.
- Exploitability depends on whether the specific USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink returns `false` on failure rather than reverting. If it follows the non-reverting ERC-20 pattern, exploitation is trivially reachable. [4](#0-3) 

---

### Recommendation

Replace the raw `transferFrom` call with the protocol's own `safeTransferFrom` wrapper from `ERC20Helper`, consistent with every other transfer site in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ContractOwner` already imports and uses `ERC20Helper` via `using ERC20Helper for IERC20Base`, so no additional imports are needed. [5](#0-4) 

---

### Proof of Concept

1. A subaccount owner's DDA (`directDepositV1Address[subaccount]`) holds 1000 USDCe.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC balance/allowance.
3. `IERC20Base(usdc).transferFrom(attacker, dda, 1000)` returns `false` (no revert).
4. Return value is ignored; execution continues.
5. `DirectDepositV1(dda).withdraw(usdcE)` transfers 1000 USDCe from DDA → `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` transfers 1000 USDCe from `ContractOwner` → attacker.
7. Attacker holds 1000 USDCe; DDA is empty; subaccount owner's pending deposit is lost. [6](#0-5)

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
