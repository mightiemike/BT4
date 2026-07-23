Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the address supplying tokens) and validates only `owner` (the LP position beneficiary) against the per-pool allowlist. Any un-allowlisted address can bypass the guard by nominating any allowlisted address as the LP position `owner`, injecting liquidity into a permissioned pool without approval.

## Finding Description
`ExtensionCalling._beforeAddLiquidity` encodes and forwards both `sender` (the `addLiquidity` caller supplying tokens) and `owner` (the LP position beneficiary) to every registered extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L88-98
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
```

`DepositAllowlistExtension.beforeAddLiquidity` receives both but drops `sender` (unnamed first parameter) and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` and discards the second parameter:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The exploit path:
1. Pool `P` has `DepositAllowlistExtension` configured; pool admin sets `allowedDepositor[P][Alice] = true`.
2. Bob (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = <Bob's tokens>, ...)`.
3. The pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
4. The extension evaluates `allowedDepositor[P][Alice] == true` → check passes, no revert.
5. Bob's tokens enter pool `P`; Alice holds the LP position.
6. Bob and Alice collude: Alice removes liquidity and returns tokens to Bob off-chain.

The `sender` (Bob) is never validated at any point in the call chain. The `onlyPool` modifier present on the base class `beforeAddLiquidity` is also absent from the override, though this secondary omission does not independently enable the bypass since `msg.sender` is used as the pool key and no allowlist entry would exist for an arbitrary direct caller.

## Impact Explanation
This is an admin-boundary break: the pool admin's configured access control (e.g., KYC-gated, institutional-only liquidity) is bypassed by any unprivileged address. An un-allowlisted address can supply tokens to a permissioned pool, alter the pool's bin depth ladder affecting oracle-anchored pricing for subsequent swaps, and via collusion with any allowlisted address achieve full economic participation in the pool with zero allowlist enforcement. The corrupted value is the `allowedDepositor` access control decision — it evaluates `true` for an un-allowlisted `sender` because the wrong address (`owner`) is checked.

## Likelihood Explanation
The bypass requires only knowing one allowlisted address, which is trivially discoverable on-chain via `AllowedToDepositSet` events. No special role or privilege is needed — any EOA or contract can execute this. Any pool that has at least one allowlisted depositor (i.e., any meaningfully configured pool) is vulnerable. The attack is repeatable with zero friction.

## Recommendation
Change the `beforeAddLiquidity` check to validate `sender` (the actual depositor) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`. Also restore the `onlyPool` modifier on the override:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    onlyPool
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict both the caller and the position owner, check both addresses explicitly.

## Proof of Concept
```
Setup:
  Pool P has DepositAllowlistExtension E configured.
  Pool admin calls: setAllowedToDeposit(P, Alice, true)
  Bob is NOT on the allowlist.

Attack:
  Bob calls pool.addLiquidity(owner=Alice, salt=0, deltas=<Bob's tokens>, ...)

Extension check (DepositAllowlistExtension.sol L38):
  msg.sender                 = P      (pool, correct)
  allowAllDepositors[P]      = false
  allowedDepositor[P][Alice] = true   ← owner checked, not sender (Bob)
  → check passes, no revert

Result:
  Bob's tokens enter pool P.
  Alice holds the LP position (owner=Alice, salt=0).
  Bob has bypassed the deposit allowlist entirely.
  Alice removes liquidity and returns tokens to Bob off-chain.
  Bob achieves full economic participation in a permissioned pool without approval.

Foundry test sketch:
  1. Deploy factory, pool P with DepositAllowlistExtension.
  2. vm.prank(poolAdmin); ext.setAllowedToDeposit(P, alice, true);
  3. vm.prank(bob); pool.addLiquidity(alice, 0, deltas, "");
  4. Assert: call succeeds (no NotAllowedToDeposit revert).
  5. Assert: alice's LP position has non-zero liquidity.
```