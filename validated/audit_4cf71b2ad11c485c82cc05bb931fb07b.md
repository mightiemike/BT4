### Title
Unchecked ERC20 `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Drain from Any DDA — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking its return value. The function has no `onlyOwner` guard — any caller on chain 57073 can invoke it. If the USDC `transferFrom` fails silently (returns `false`), the function continues, withdraws usdcE from the target DDA, and delivers it to the caller — with no USDC ever deposited in exchange.

---

### Finding Description

`replaceUsdcEWithUsdc` is a three-step swap function:

1. Pull `balance` USDC from `msg.sender` into `directDepositV1` via `transferFrom`.
2. Withdraw `balance` usdcE from `directDepositV1` to `ContractOwner` via `DirectDepositV1.withdraw`.
3. Forward the usdcE from `ContractOwner` to `msg.sender` via `safeTransfer`.

Step 1 uses a raw, unchecked `transferFrom`:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

The return value (`bool`) is discarded. `IERC20Base.transferFrom` is declared to return `bool`: [2](#0-1) 

Steps 2 and 3 execute unconditionally regardless of whether step 1 succeeded. Step 3 uses `safeTransfer` (which does check the return value), creating an explicit asymmetry: [3](#0-2) 

The codebase already provides `ERC20Helper.safeTransferFrom` which wraps the low-level call and reverts on failure: [4](#0-3) 

This helper is used correctly everywhere else in the protocol (e.g., `EndpointStorage.safeTransferFrom`): [5](#0-4) 

The function has **no access control modifier** — only a chain ID gate: [6](#0-5) 

---

### Impact Explanation

If `IERC20Base(usdc).transferFrom` returns `false` instead of reverting (e.g., caller has zero allowance and the USDC implementation at `0x2D270e6886d130D724215A266106e6832161EAEd` is an upgradeable proxy that changes behavior, or any non-reverting failure path is triggered), the execution continues. The DDA loses its entire usdcE balance — transferred to the attacker — while no USDC is deposited in return. The corrupted asset delta is: DDA usdcE balance decremented by `balance`, attacker usdcE balance incremented by `balance`, with zero USDC compensation.

---

### Likelihood Explanation

The function is externally callable by any address on chain 57073 with no privilege requirement. The USDC address is hardcoded as a specific contract on Ink chain; if it is an upgradeable proxy (common for Circle USDC deployments), a future upgrade could introduce a non-reverting failure path. Even absent an upgrade, the structural absence of a return-value check is a latent vulnerability that violates the protocol's own safety pattern used everywhere else.

---

### Recommendation

Replace the raw `transferFrom` call with the protocol's existing `safeTransferFrom` helper:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` is already imported via `using ERC20Helper for IERC20Base` at the top of `ContractOwner`: [7](#0-6) 

No new dependency is needed — the fix is a one-word change.

---

### Proof of Concept

1. Attacker identifies a `subaccount` whose DDA holds non-zero usdcE (`balance > 0`).
2. Attacker calls `replaceUsdcEWithUsdc(subaccount)` on chain 57073 **without approving any USDC** (or with zero USDC balance).
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, balance)` returns `false` (silent failure — no revert).
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes: usdcE is transferred from DDA → `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, balance)` executes: usdcE is transferred from `ContractOwner` → attacker.
6. Attacker receives `balance` usdcE. DDA is drained. Zero USDC was ever deposited. [8](#0-7)

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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

**File:** core/contracts/EndpointStorage.sol (L95-101)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```
