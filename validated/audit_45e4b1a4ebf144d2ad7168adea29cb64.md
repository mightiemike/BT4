### Title
Unchecked `transferFrom()` Return Value Enables Token Drain in `replaceUsdcEWithUsdc` - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` uses a raw, unchecked `IERC20Base.transferFrom()` call to pull USDC from `msg.sender` before releasing `usdcE` back to them. If the USDC token returns `false` instead of reverting on failure, the function continues execution and transfers `usdcE` out of the `DirectDepositV1` contract to the caller without any USDC having been received — draining the vault's bridged asset.

---

### Finding Description

`replaceUsdcEWithUsdc` is a permissionless `external` function (gated only by `block.chainid == 57073`) that is intended to swap `usdcE` held in a `DirectDepositV1` (DDA) contract for native `usdc` provided by the caller:

```solidity
// ContractOwner.sol line 616
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [1](#0-0) 

The `transferFrom` at line 616 is a direct interface call whose `bool` return value is silently discarded. The protocol already provides `ERC20Helper.safeTransferFrom`, which wraps the call and reverts on `false` or missing return data: [2](#0-1) 

`safeTransferFrom` is used correctly everywhere else in the codebase — including in `EndpointStorage.chargeSlowModeFee` and `EndpointStorage.safeTransferFrom`: [3](#0-2) 

The `withdraw` call on line 617 succeeds unconditionally because `ContractOwner` is the `Ownable` owner of every `DirectDepositV1` it deploys: [4](#0-3) 

The final `safeTransfer` on line 618 then sends `usdcE` to `msg.sender` regardless of whether the USDC pull succeeded.

---

### Impact Explanation

If the USDC token at the hardcoded address `0x2D270e6886d130D724215A266106e6832161EAEd` (Ink chain) returns `false` on a failed `transferFrom` rather than reverting — a known behavior of tokens such as USDT, BNB, and others — an unprivileged caller can:

1. Call `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose DDA holds `usdcE`.
2. Provide zero USDC allowance; the `transferFrom` silently returns `false`.
3. Receive the full `usdcE` balance of the DDA for free.

The corrupted asset delta is the complete `usdcE` balance of the targeted `DirectDepositV1` contract, transferred to the attacker with no USDC consideration. This constitutes direct token theft from user-owned DDA vaults.

---

### Likelihood Explanation

- The function is `external` with no `onlyOwner` or `onlyDeployer` guard — any address on Ink chain (chainid 57073) can call it.
- The USDC token address is hardcoded; its exact revert-vs-return-false behavior on Ink is the determining factor.
- Even if the current USDC implementation reverts, the pattern is a latent vulnerability: a token upgrade or a future product addition using a non-reverting token would immediately activate the exploit path.
- The inconsistency with the rest of the codebase (which uniformly uses `safeTransferFrom`) confirms this is an unintentional omission.

---

### Recommendation

Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, consistent with every other transfer site in the protocol:

```solidity
// Before (ContractOwner.sol line 616)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ContractOwner` already imports and uses `ERC20Helper` via `using ERC20Helper for IERC20Base`: [5](#0-4) 

No additional imports are required.

---

### Proof of Concept

1. A user's DDA (`directDepositV1Address[subaccount]`) holds 1000 `usdcE`.
2. Attacker calls `replaceUsdcEWithUsdc(subaccount)` on Ink chain with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(attacker, dda, 1000)` returns `false` (non-reverting token); return value is ignored.
4. `DirectDepositV1(dda).withdraw(usdcE)` succeeds — `ContractOwner` is the DDA owner — transferring 1000 `usdcE` to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` sends 1000 `usdcE` to the attacker.
6. Attacker receives 1000 `usdcE` at zero cost; the DDA is drained. [6](#0-5)

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

**File:** core/contracts/EndpointStorage.sol (L83-101)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }

    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
