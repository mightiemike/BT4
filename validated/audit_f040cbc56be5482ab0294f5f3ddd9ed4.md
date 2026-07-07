### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Drain of DDA usdcE Without Providing USDC - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` is an unpermissioned external function that uses a bare, unchecked `IERC20Base(usdc).transferFrom(...)` to pull USDC from the caller before releasing usdcE from the DirectDepositV1 (DDA) contract. The `ERC20Helper` library already provides `safeTransferFrom` for exactly this purpose, but it is not used here. If the USDC token on the target chain returns `false` instead of reverting on a failed transfer, an attacker can receive the DDA's entire usdcE balance without providing any USDC.

---

### Finding Description

`ContractOwner.replaceUsdcEWithUsdc()` is gated only by a chain ID check (`block.chainid == 57073`) and is otherwise callable by any external address with no ownership or role restriction. [1](#0-0) 

The function's intended logic is an atomic swap: pull USDC from the caller into the DDA, then release usdcE from the DDA back to the caller. The critical step is line 616:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value of `transferFrom` is never checked. Execution continues unconditionally to:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [2](#0-1) 

`DirectDepositV1.withdraw` uses the contract's own `safeTransfer` helper to move the full usdcE balance from the DDA to `ContractOwner` (with `msg.sender` being `ContractOwner` from the DDA's perspective), and then `ContractOwner` forwards it to the original caller via `safeTransfer`. [3](#0-2) 

The `ERC20Helper` library, which is already imported and bound to `IERC20Base` via `using ERC20Helper for IERC20Base`, provides a `safeTransferFrom` that checks the return value and reverts on failure. It is used elsewhere in the codebase but not here. [4](#0-3) 

---

### Impact Explanation

If the USDC token at the hardcoded address (`0x2D270e6886d130D724215A266106e6832161EAEd` on chain 57073 / Ink) returns `false` on a failed `transferFrom` rather than reverting, an attacker can:

1. Call `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose DDA holds usdcE.
2. The `transferFrom` silently fails (returns `false`), so no USDC is transferred from the attacker.
3. The DDA's entire usdcE balance is transferred to `ContractOwner` and then forwarded to the attacker.

The corrupted asset delta is the full usdcE balance of the targeted DDA, transferred to the attacker at zero cost. The DDA is left with no usdcE, and the subaccount's pending deposit is permanently lost.

---

### Likelihood Explanation

The function is externally callable by any address on Ink chain with no access control beyond the chain ID check. The exploitability depends on whether the specific USDC deployment at the hardcoded address returns `false` on failure rather than reverting. Non-standard or bridged USDC variants (common on newer L2s) are known to exhibit this behavior. The codebase itself acknowledges this risk class by providing `ERC20Helper.safeTransferFrom` and using it in other transfer paths, making the omission here a concrete inconsistency rather than a theoretical concern.

---

### Recommendation

Replace the bare `transferFrom` call with the already-available `safeTransferFrom` from `ERC20Helper`:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [5](#0-4) 

---

### Proof of Concept

1. A DDA exists for `subaccount` on Ink chain (chainid 57073) with a non-zero usdcE balance.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC balance or zero allowance.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, balance)` returns `false` (no revert).
4. Execution continues: `DirectDepositV1(directDepositV1).withdraw(usdcE)` moves the full usdcE balance to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, balance)` sends the full usdcE balance to the attacker.
6. Attacker receives `balance` usdcE tokens. The DDA holds zero usdcE. The subaccount's collateral deposit is lost.

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
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
