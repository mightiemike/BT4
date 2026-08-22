### Title
Front-Runnable Token-Name Uniqueness Check Allows DoS of Legitimate AssetIssue Transactions - (File: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`)

### Summary
`AssetIssueActuator` lets the token *name* supplied by the transaction sender act as a de-facto unique identifier for the asset (when the `ALLOW_SAME_TOKEN_NAME` chain parameter is disabled). Because uniqueness is enforced only by checking store state at validation time, and the desired name is fully attacker-observable once the transaction is broadcast to the network, a malicious actor can front-run a pending `AssetIssueContract` by submitting their own transaction with the identical name and a higher energy/bandwidth priority, causing the original transaction to fail deterministically with `"Token exists"`.

### Finding Description
`AssetIssueActuator.validate()` rejects a transaction if a token with the same name already exists in `AssetIssueStore`: [1](#0-0) 

The name itself is entirely user-supplied (only checked for basic format via `TransactionUtil.validAssetName`), and is used directly as the DB key returned by `AssetIssueCapsule.createDbKey()`: [2](#0-1) 

`execute()` then persists the asset keyed by that name (and internally by an auto-incremented `tokenIdNum`, which itself is not vulnerable, but doesn't protect the name-based path): [3](#0-2) 

This is directly analogous to the reported bug class: the contract relies on a user-provided value (here, the asset name) to establish uniqueness for a state-changing operation, instead of generating the identifier internally. Any broadcast transaction is visible in the mempool before being included in a block, so an attacker monitoring pending transactions can observe the desired `name` field of a victim's `AssetIssueContract`, then submit a competing `AssetIssueContract` with the same `name` and sufficient priority (e.g., higher fee/earlier broadcast, or via direct block producer relationships) to have it processed first. Once the attacker's transaction executes and inserts the name into `AssetIssueStore`, the victim's original transaction will fail validation with `"Token exists"`, wasting the victim's effort/fees and preventing them from ever issuing that named asset while the attacker holds it.

### Impact Explanation
- Denial of service against a specific account's ability to issue a chosen token name: the victim's carefully-prepared `AssetIssueContract` transaction is guaranteed to fail once front-run, and they cannot retry with the same name since the attacker's entry stays in the store.
- Reputational/economic griefing: an attacker can squat on names intended for legitimate projects, similar to typosquatting, without needing any privileged role — this is exploitable by any ordinary account holder able to broadcast transactions.
- This does not directly corrupt consensus or leak keys, but it is a legitimate availability/integrity issue for the TRC10 token-issuance feature.

### Likelihood Explanation
- Exploitability requires only the ability to observe pending mempool transactions (public information for any full node) and to broadcast a transaction — no privileged actor or protocol-level access is needed.
- Likelihood is reduced by the fact that on most long-running networks (including likely current mainnet configuration) the `ALLOW_SAME_TOKEN_NAME` proposal has already been activated, in which case `dynamicStore.getAllowSameTokenName() != 0` and this specific name-collision code path is bypassed (assets are then keyed only by the internally-generated numeric ID). However, the vulnerable code path remains fully reachable on any network where this proposal has not been activated (e.g., private/permissioned deployments, test networks, or during the window before the proposal activates on a given chain), and the flaw is inherent to the actuator logic itself.

### Recommendation
- Do not use user-supplied identifiers (asset name) as the sole uniqueness key for a state-changing resource when `ALLOW_SAME_TOKEN_NAME` is disabled. Continue to rely exclusively on the internally generated, monotonically increasing `tokenIdNum` (as already used for the V2 store) as the canonical unique identifier, and treat the name field purely as metadata/display value, never as a DB key for uniqueness enforcement.
- If backward compatibility with the legacy name-keyed store is required, consider allowing the same name to be claimed by multiple concurrent submitters and resolving ties by transaction ordering rules that are not attacker-predictable/front-runnable (e.g., reserving names to the account instead of first-come-first-served on raw broadcast order), or requiring a commit-reveal scheme for name registration.

### Proof of Concept
1. Victim broadcasts `AssetIssueContract` with `name = "MYTOKEN"` while `ALLOW_SAME_TOKEN_NAME == 0`.
2. Attacker observes the pending transaction in the public mempool and immediately broadcasts their own `AssetIssueContract` with the identical `name = "MYTOKEN"`, using a higher bandwidth/energy priority or otherwise ensuring earlier block inclusion.
3. Attacker's transaction is processed first; `AssetIssueActuator.execute()` inserts `"MYTOKEN"` into `AssetIssueStore` keyed by the raw name (`AssetIssueActuator.java:80-83`).
4. Victim's transaction is processed next; `AssetIssueActuator.validate()` at `AssetIssueActuator.java:210-214` finds the name already present and throws `ContractValidateException("Token exists")`, permanently blocking the victim from issuing a token under that name.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L72-87)
```java
      long tokenIdNum = dynamicStore.getTokenIdNum();
      tokenIdNum++;
      assetIssueCapsule.setId(Long.toString(tokenIdNum));
      assetIssueCapsuleV2.setId(Long.toString(tokenIdNum));
      dynamicStore.saveTokenIdNum(tokenIdNum);

      if (dynamicStore.getAllowSameTokenName() == 0) {
        assetIssueCapsuleV2.setPrecision(0);
        assetIssueStore
            .put(assetIssueCapsule.createDbKey(), assetIssueCapsule);
        assetIssueV2Store
            .put(assetIssueCapsuleV2.createDbV2Key(), assetIssueCapsuleV2);
      } else {
        assetIssueV2Store
            .put(assetIssueCapsuleV2.createDbV2Key(), assetIssueCapsuleV2);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L210-214)
```java
    if (dynamicStore.getAllowSameTokenName() == 0
        && assetIssueStore.get(assetIssueContract.getName().toByteArray())
        != null) {
      throw new ContractValidateException("Token exists");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java (L111-120)
```java
  public byte[] createDbKey() {
//    long order = getOrder();
//    if (order == 0) {
//      return getName().toByteArray();
//    }
//    String name = new String(getName().toByteArray(), Charset.forName("UTF-8"));
//    String nameKey = createDbKeyString(name, order);
//    return nameKey.getBytes();
    return getName().toByteArray();
  }
```
