### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables USDC-e Drain Without Providing USDC — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` call without checking its return value. The function has no access control beyond a chain ID check, making it callable by any unprivileged user. If the USDC token at the hardcoded address returns `false` on failure rather than reverting, an attacker can drain USDC-e from any `DirectDepositV1` contract without depositing any USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no `onlyOwner` or `onlyDeployer` modifier. Its only guard is `require(block.chainid == 57073, ERR_UNAUTHORIZED)`. [1](#0-0) 

The function's logic is:
1. Read the USDC-e balance held by `directDepositV1`.
2. Call `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` — **raw call, return value discarded**.
3. Call `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulls all USDC-e from `directDepositV1` into `ContractOwner`.
4. Call `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sends USDC-e to the caller. [2](#0-1) 

The rest of the codebase consistently wraps all token transfers through `ERC20Helper.safeTransferFrom`, which checks both the call success flag and the decoded boolean return value: [3](#0-2) 

The single instance at line 616 bypasses this wrapper entirely, calling `transferFrom` directly on the `IERC20Base` interface and ignoring the `bool` return. [4](#0-3) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (chain 57073) returns `false` on a failed transfer rather than reverting — a known behavior of non-standard or bridged ERC20 tokens — the attacker's step 2 silently fails. Steps 3 and 4 still execute: USDC-e is withdrawn from `directDepositV1` into `ContractOwner`, then forwarded to the attacker. The attacker receives the full USDC-e balance of the targeted `DirectDepositV1` contract without providing any USDC. This is a direct asset theft of USDC-e held in any `directDepositV1` address registered in `directDepositV1Address`. [1](#0-0) 

---

### Likelihood Explanation

The function is callable by any address on chain 57073 with no further restriction. The attacker only needs to know a `subaccount` that has a registered `directDepositV1` with a non-zero USDC-e balance — both are observable on-chain. The exploitability depends on whether the specific USDC deployment at the hardcoded address returns `false` on failure; bridged or wrapped USDC variants on non-mainnet chains frequently exhibit this behavior. The combination of zero access control and an unchecked return value makes this a realistic, externally triggerable exploit. [5](#0-4) 

---

### Recommendation

Replace the raw `transferFrom` call with the project's own `ERC20Helper.safeTransferFrom` wrapper, consistent with every other transfer site in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [3](#0-2) 

Additionally, consider adding an `onlyOwner` modifier to `replaceUsdcEWithUsdc`, since it is a privileged migration operation and should not be callable by arbitrary users. [6](#0-5) 

---

### Proof of Concept

1. A `DirectDepositV1` contract exists for some `subaccount` on chain 57073, holding 1000 USDC-e.
2. Attacker (with zero USDC balance) calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, 1000)` returns `false` (attacker has no USDC); the return value is not checked, execution continues.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` succeeds — `ContractOwner` is the owner of `DirectDepositV1`, so the `onlyOwner` check passes; 1000 USDC-e is transferred to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` sends 1000 USDC-e to the attacker.
6. Attacker has stolen 1000 USDC-e, depositing nothing. [2](#0-1) [7](#0-6)

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
