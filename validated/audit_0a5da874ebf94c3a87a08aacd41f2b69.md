### Title
Contract-mediated `unfreezeBalanceV2`/`freezeBalanceV2` TVM native contracts key the `UNFREEZE_MAX_TIMES` quota on the calling contract's own context address, letting any caller exhaust it and DoS all other users routed through the same pooling contract - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java])

### Summary
The BakerFi finding shows that a shared "router" contract, which is `msg.sender` when it forwards deposits into the vault, is treated by the vault as a single depositor subject to a per-depositor cap. Because many independent end-users route through the same contract, one user (or an attacker) can drive that shared identity to its cap and block deposits for everyone else using the router.

The same structural pattern exists in java-tron's TVM native "freeze/unfreeze" precompiled operations. When a smart contract calls `freezeBalanceV2`/`unfreezeBalanceV2` (e.g. a staking-pool or "resource router" contract that stakes TRX and manages resources on behalf of many end users), the "owner" for accounting purposes is `Program.getContextAddress()` — i.e. the calling contract itself, not the individual end user who triggered the call. The unfreeze quota (`UNFREEZE_MAX_TIMES`, checked via `AccountCapsule.getUnfreezingV2Count`) is enforced per-account, keyed on that shared context address. [1](#0-0) [2](#0-1) 

### Finding Description
`Program.unfreezeBalanceV2` sets `param.setOwnerAddress(owner)` where `owner = getContextAddress()`, i.e. the address of the contract executing the opcode (the pooling/router contract), not any per-user address supplied as an argument: [1](#0-0) 

`UnfreezeBalanceV2Processor.validate` then checks a global, per-owner-account quota:
```
int unfreezingCount = accountCapsule.getUnfreezingV2Count(now);
if (UnfreezeBalanceV2Actuator.getUNFREEZE_MAX_TIMES() <= unfreezingCount) {
  throw new ContractValidateException("Invalid unfreeze operation, unfreezing times is over limit");
}
``` [3](#0-2) 

`getUnfreezingV2Count` counts all not-yet-expired pending unfreeze entries on the account (default unfreeze delay is many days, e.g. `FROZEN_PERIOD`-scaled, configurable via `unfreezeDelayDays`): [4](#0-3) 

Just like `VaultBase._depositInternal`, which reads `balanceOf(msg.sender)` where `msg.sender` is the shared `VaultRouter`, this native-contract accounting reads a resource counter keyed by the shared calling contract's own address (`getContextAddress()`), rather than by the true originating end user. Any smart contract built to pool TRX freezing/unfreezing on behalf of multiple independent users (a common integration pattern for staking-as-a-service or "energy rental" dApps) will have all of its users' unfreeze operations counted against the single pooling contract's `UNFREEZE_MAX_TIMES` quota. One user (or an attacker interacting with the pool) can create enough pending unfreeze entries to hit the cap, after which the entire pooling contract — and therefore every other legitimate user routed through it — cannot call `unfreezeBalanceV2` again until enough pending entries expire (potentially many days later, per `unfreezeDelayDays`/`FROZEN_PERIOD`).

This mirrors the BakerFi root cause precisely: a shared intermediary contract is the effective "depositor"/"unfreezer" as far as the protocol's per-account limit is concerned, so the limit is trivially exhausted by aggregate usage through the router rather than by any single legitimate actor, causing a denial of service for all other users of that shared contract.

### Impact Explanation
Any dApp/contract that pools TRX freeze/unfreeze operations for multiple end users (staking pools, energy marketplaces, "freeze-as-a-service" contracts) is exposed to a DoS: once the shared contract's `UNFREEZE_MAX_TIMES` quota is filled (by normal usage or deliberately by an attacker transacting through the same pool contract), no user of that pool can unfreeze TRX/resources via that contract until existing pending unfreeze entries expire. This is a reachable, unprivileged DoS via ordinary broadcast transactions (`TriggerSmartContract`) invoking the `unfreezeBalanceV2` TVM opcode — no special privilege is required, matching the "DoS via ... protocol implementation" acceptance criterion. It does not cause fund loss or consensus divergence, but it does block a core resource-management function for every user relying on the shared contract.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a smart contract that implements pooled freeze/unfreeze on behalf of multiple users via the TVM native `freezeBalanceV2`/`unfreezeBalanceV2` opcodes, and (b) enough transaction volume (organic or attacker-driven) to fill the fixed `UNFREEZE_MAX_TIMES` slots. This is a realistic pattern for staking-pool style contracts on java-tron, and an attacker who is also a user of such a pool can deliberately trigger many small unfreeze calls to fill the quota cheaply, since each call only needs to be `>0` TRX and reference an existing frozen balance.

### Recommendation
- Track the unfreeze/freeze quota per true originating end user rather than solely per `getContextAddress()`, e.g. by having pooling contracts maintain their own internal per-user accounting layered on top of the shared on-chain quota, or
- Expose/require an explicit "on behalf of" address parameter for these native contract calls so quotas can be enforced against the real actor when invoked through an intermediary, and/or
- Document clearly (and enforce defensively in higher-level tooling) that `UNFREEZE_MAX_TIMES` is a per-`msg.sender`-context resource and must not be treated as a per-end-user limit by any contract that pools TRX resource operations for multiple users.

### Proof of Concept
1. Deploy a pooling contract `Pool` that lets arbitrary users call `Pool.unfreeze(resourceType, amount)`, which internally invokes the TVM `unfreezeBalanceV2` opcode; the opcode is issued with `owner = getContextAddress() = Pool's own address` (per `Program.unfreezeBalanceV2`).
2. Have `Pool` freeze TRX for bandwidth/energy on its own behalf via `freezeBalanceV2` (also keyed to `Pool`'s context address) to build up a large frozen balance servicing many users.
3. Any user (or attacker) repeatedly calls `Pool.unfreeze(...)` with small `unfreezeBalance` amounts (each valid, since only positive amount and existing frozen balance are checked) — each call adds one entry to `Pool`'s `unfrozenV2List`.
4. Once `Pool`'s pending unfreeze entries reach `UnfreezeBalanceV2Actuator.getUNFREEZE_MAX_TIMES()`, per `UnfreezeBalanceV2Processor.validate` (line 51-54) every subsequent unfreeze call from `Pool` — for any user — reverts with `"Invalid unfreeze operation, unfreezing times is over limit"`.
5. `Pool` remains unable to service any user's unfreeze request until enough of the pending `UnFreezeV2` entries pass their `unfreezeExpireTime` (governed by `unfreezeDelayDays`), which can be a substantial delay, producing a sustained DoS for all pool users.

Note: I was unable to fully verify the exact numeric default of `UNFREEZE_MAX_TIMES` from the retrieved snippets (only its usage sites were confirmed, not its literal value), and I could not directly inspect an example pooling/staking contract implementation in the java-tron test/example suite to confirm this exact usage pattern is deployed on-chain today — these remain assumptions based on the documented native-contract semantics.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2048-2065)
```java
  public boolean unfreezeBalanceV2(DataWord unfreezeBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner,
        unfreezeBalance.longValue(), null,
        "unfreezeBalanceV2For" + convertResourceToString(resourceType), nonce, null);

    try {
      UnfreezeBalanceV2Param param = new UnfreezeBalanceV2Param();
      param.setOwnerAddress(owner);
      param.setUnfreezeBalance(unfreezeBalance.sValue().longValueExact());
      param.setResourceType(parseResourceCodeV2(resourceType));

      UnfreezeBalanceV2Processor processor = new UnfreezeBalanceV2Processor();
      processor.validate(param, repository);
      long unfreezeExpireBalance = processor.execute(param, repository);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L39-54)
```java
    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    if (accountCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + "] does not exist");
    }
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    int unfreezingCount = accountCapsule.getUnfreezingV2Count(now);
    if (UnfreezeBalanceV2Actuator.getUNFREEZE_MAX_TIMES() <= unfreezingCount) {
      throw new ContractValidateException("Invalid unfreeze operation, unfreezing times is over limit");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L679-688)
```java
  public int getUnfreezingV2Count(long now) {
    int count = 0;
    List<UnFreezeV2> unFreezeV2List = account.getUnfrozenV2List();
    for (UnFreezeV2 item : unFreezeV2List) {
      if (item.getUnfreezeExpireTime() > now) {
        count++;
      }
    }
    return count;
  }
```
