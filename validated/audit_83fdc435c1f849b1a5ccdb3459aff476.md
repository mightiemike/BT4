Now I have all the information needed. Let me trace the full call path precisely.

**Call path:**
1. `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` — attacker supplies arbitrary `owner`
2. → `pool.addLiquidity(owner, salt, deltas, ...)` (line 195 of `MetricOmmPoolLiquidityAdder`)
3. → `MetricOmmPool._beforeAddLiquidity(msg.sender, owner, ...)` where `msg.sender` = `MetricOmmPoolLiquidityAdder` (line 191 of `MetricOmmPool`)
4. → `ExtensionCalling._beforeAddLiquidity` encodes `(sender=MetricOmmPoolLiquidityAdder, owner=attacker-supplied, ...)` (line 97 of `ExtensionCalling`)
5. → `DepositAllowlistExtension.beforeAddLiquidity(address /*sender ignored*/, address owner, ...)` checks `allowedDepositor[msg.sender][owner]` — `sender` is silently discarded, only `owner` is checked (line 38 of `DepositAllowlistExtension`)

The `sender` parameter (the actual `addLiquidity` caller, i.e., `MetricOmmPoolLiquidityAdder`) is the first unnamed/discarded parameter. The hook only checks `owner` (the position owner), which the attacker fully controls via `addLiquidityExactShares`. `_validateOwner` only rejects `address(0)`.

---

Audit Report

## Title
Deposit Allowlist Bypass via Owner/Payer Separation in `addLiquidityExactShares` - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` (position owner) parameter against the allowlist while silently discarding the `sender` parameter (the actual `addLiquidity` caller). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller supply an arbitrary `owner` address, an unprivileged depositor can bypass the allowlist by naming an already-allowlisted address as `owner`, paying the tokens themselves, and minting LP shares into that address's position — defeating the entire deposit gate.

## Finding Description
**Root cause — wrong identity checked:**

`DepositAllowlistExtension.beforeAddLiquidity` (line 32–42) receives two identity parameters: `sender` (the direct caller of `pool.addLiquidity`) and `owner` (the position owner). The function signature discards `sender` (unnamed first argument) and gates only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

**Exploit flow:**

1. Pool admin configures `DepositAllowlistExtension` with `allowedDepositor[pool][alice] = true` and `allowedDepositor[pool][attacker] = false`.
2. Attacker calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, extensionData)`.
3. `_validateOwner(alice)` passes (non-zero check only, line 247–249).
4. `_addLiquidity(pool, alice, salt, deltas, msg.sender=attacker, ...)` is called (line 67).
5. Pool's `addLiquidity(alice, salt, deltas, ...)` fires `_beforeAddLiquidity(MetricOmmPoolLiquidityAdder, alice, ...)` (line 191 of `MetricOmmPool`).
6. `ExtensionCalling._beforeAddLiquidity` encodes `(sender=MetricOmmPoolLiquidityAdder, owner=alice, ...)` and calls the extension (line 97 of `ExtensionCalling`).
7. Extension checks `allowedDepositor[pool][alice]` → `true`. Hook passes.
8. Attacker's tokens are pulled (via callback, payer = attacker), LP shares minted to `alice`.

**Why existing guards fail:**
- `_validateOwner` only rejects `address(0)` — any allowlisted address is accepted as `owner`.
- The `sender` parameter passed to the hook is `MetricOmmPoolLiquidityAdder`, not the attacker's EOA, so even if `sender` were checked it would not identify the real depositor without further unwrapping.
- `BaseMetricExtension.onlyPool` only verifies the hook is called by a registered pool — it does not constrain which `owner` or `sender` values are passed.

## Impact Explanation
The deposit allowlist is the primary access-control mechanism for restricted pools. Its bypass allows any unprivileged address to add liquidity to a pool that the admin intended to gate, breaking the core pool functionality invariant that only allowlisted depositors can mint LP shares. The attacker pays their own tokens and the position accrues to the named `owner`, but the allowlist gate — the only mechanism preventing unauthorized capital from entering the pool — is rendered ineffective. This constitutes broken core pool functionality under the contest's allowed impact gate.

## Likelihood Explanation
The attack requires no special privileges, no flash loan, and no oracle manipulation. Any unprivileged address can call `addLiquidityExactShares` with a publicly observable allowlisted address as `owner`. The allowlist of a pool is readable on-chain via `allowedDepositor`. The attack is repeatable at any time the pool is not paused and is reachable through the public `MetricOmmPoolLiquidityAdder` periphery contract.

## Recommendation
The hook must gate on the economically relevant depositor, not the position owner. Two complementary fixes:

1. **Check `sender` instead of (or in addition to) `owner`** in `DepositAllowlistExtension.beforeAddLiquidity`. `sender` is the direct caller of `pool.addLiquidity` (i.e., `MetricOmmPoolLiquidityAdder` when routed through the adder). If the intent is to gate the end-user, the adder must forward the real payer in `extensionData` and the extension must decode and verify it, or the pool must pass the original `msg.sender` through a trusted channel.

2. **Restrict `owner` to `msg.sender` in `addLiquidityExactShares`** (or require explicit pool-admin approval for owner != sender), so the payer and position owner cannot be separated by an unprivileged caller.

## Proof of Concept
```solidity
// Foundry test sketch
function test_allowlistBypass() public {
    // Setup: alice is allowlisted, attacker is not
    vm.prank(poolAdmin);
    depositAllowlist.setAllowedToDeposit(address(pool), alice, true);

    // Attacker (not allowlisted) deposits via adder, naming alice as owner
    vm.startPrank(attacker);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);

    // Should revert with NotAllowedToDeposit — but does NOT
    adder.addLiquidityExactShares(
        address(pool),
        alice,          // owner = allowlisted address
        0,
        deltas,
        type(uint256).max,
        type(uint256).max,
        ""
    );
    vm.stopPrank();

    // Attacker's tokens were consumed; alice has LP shares she never requested
    assertGt(pool.positionShares(alice, 0, binIdx), 0);
}
```