### Title
Unchecked `transferFrom` Return Value Enables Token Drain in `replaceUsdcEWithUsdc` - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `transferFrom` call on the USDC token without checking its boolean return value. Because the function has no access control beyond a chain-ID gate, any unprivileged caller on the Ink chain can invoke it. If the `transferFrom` silently returns `false` instead of reverting, the caller receives usdcE from a victim's `DirectDepositV1` contract without ever depositing USDC, draining the victim's collateral staging balance.

---

### Finding Description

`replaceUsdcEWithUsdc` is a public migration helper intended to swap usdcE held in a `DirectDepositV1` (DDA) contract for canonical USDC. The function:

1. Reads the usdcE balance held by the target DDA.
2. Calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` — **return value discarded**.
3. Calls `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulls all usdcE from the DDA to `ContractOwner`.
4. Calls `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sends usdcE to the caller.

Step 2 uses a bare `transferFrom` call whose boolean return is never inspected: [1](#0-0) 

By contrast, the project's own `ERC20Helper` library provides `safeTransferFrom`, which low-level-calls the token and requires `success && (data.length == 0 || abi.decode(data, (bool)))`: [2](#0-1) 

The function carries no `onlyOwner` or `onlyDeployer` modifier — only a `block.chainid == 57073` guard — making it reachable by any unprivileged caller on the Ink chain: [3](#0-2) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (Ink chain) returns `false` on a failed `transferFrom` rather than reverting (a known pattern in non-standard or upgradeable ERC20 implementations), the execution continues past step 2. Steps 3 and 4 still execute unconditionally: the DDA's entire usdcE balance is transferred to the caller at zero cost. The corrupted asset delta is the full usdcE balance of every DDA whose subaccount is passed to the function — direct theft of user collateral staged for deposit.

---

### Likelihood Explanation

The function is callable by any address on chain 57073 with no further permission check. The exploitability depends on whether the specific USDC deployment returns `false` vs. reverts on failure. Upgradeable proxy tokens (Circle's USDC is upgradeable) can have their transfer logic changed. Even absent that, the unchecked pattern is a latent vulnerability that violates the project's own safe-transfer convention and creates a fragile dependency on the token's revert behavior.

---

### Recommendation

Replace the bare `transferFrom` call with the project's existing `safeTransferFrom` helper, consistent with how every other transfer in the codebase is handled:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ContractOwner` already imports and uses `ERC20Helper` via `using ERC20Helper for IERC20Base`: [4](#0-3) 

So the fix is a one-word change with no new dependencies.

---

### Proof of Concept

1. Attacker identifies a subaccount whose DDA holds a non-zero usdcE balance.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on Ink chain (chainid 57073).
3. The USDC `transferFrom` call at line 616 returns `false` (e.g., attacker has zero USDC allowance set and the token returns false rather than reverting).
4. The return value is ignored; execution continues.
5. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` transfers the full usdcE balance from the DDA to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` sends the usdcE to the attacker.
7. Attacker receives usdcE without providing any USDC. The victim's DDA is drained. [3](#0-2)

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
