### Title
Unsafe `transferFrom` Used Instead of `safeTransferFrom` in `replaceUsdcEWithUsdc` — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner` declares `using ERC20Helper for IERC20Base`, which provides `safeTransferFrom` as a return-value-checked wrapper. However, `replaceUsdcEWithUsdc` calls the raw, unchecked `IERC20Base.transferFrom` directly. If the USDC token on chain 57073 returns `false` on failure rather than reverting, the function proceeds to drain usdcE from the target `DirectDepositV1` contract and transfer it to the caller without the caller ever depositing USDC.

---

### Finding Description

`ContractOwner.sol` imports and binds `ERC20Helper` to `IERC20Base`: [1](#0-0) 

`ERC20Helper` provides `safeTransferFrom`, which low-level-calls `transferFrom` and requires the return value to be `true`: [2](#0-1) 

Despite this, `replaceUsdcEWithUsdc` calls the raw `IERC20Base.transferFrom` directly, ignoring its return value: [3](#0-2) 

The execution sequence is:
1. Read `balance` = usdcE held by the DDA.
2. **Raw `transferFrom`** — caller must send `balance` USDC into the DDA. Return value is silently discarded.
3. `DirectDepositV1.withdraw(usdcE)` — unconditionally pulls all usdcE out of the DDA into `ContractOwner`.
4. `safeTransfer(usdcE, msg.sender, balance)` — unconditionally sends usdcE to the caller.

If step 2 returns `false` (transfer did not occur), steps 3 and 4 still execute, giving the caller `balance` usdcE for free.

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc` with a subaccount whose DDA holds usdcE, while having zero USDC allowance or balance, can drain the entire usdcE balance of that DDA. The corrupted asset delta is: usdcE tokens belonging to the subaccount's DDA are transferred to the attacker at zero cost. This constitutes direct collateral theft from user-owned deposit accounts.

---

### Likelihood Explanation

The function is `external` with no access control beyond a `block.chainid == 57073` check, making it reachable by any unprivileged caller on the Ink chain. The exploitability depends on whether the USDC deployment at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on failure or reverts. Non-reverting ERC20 tokens are a known class; the code must not assume revert behavior. Any DDA holding usdcE is at risk.

---

### Recommendation

Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, which is already available via the `using ERC20Helper for IERC20Base` binding:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [4](#0-3) 

---

### Proof of Concept

1. A DDA for `subaccount` holds `N` usdcE tokens.
2. Attacker calls `replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, N)` returns `false`; no USDC moves. No revert.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `N` usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, N)` sends `N` usdcE to the attacker.
6. Attacker receives `N` usdcE having paid nothing. [5](#0-4)

### Citations

**File:** core/contracts/ContractOwner.sol (L18-24)
```text
import "./libraries/ERC20Helper.sol";
import "./common/Constants.sol";

contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
    error InvalidInput();
    using MathSD21x18 for int128;
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
