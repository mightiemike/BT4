### Title
Asset issuance token-name grabbing via frontrunning - (File: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`)

### Summary
`AssetIssueActuator` allows any account with sufficient balance to issue a new TRC10 token by broadcasting an `AssetIssueContract` transaction. When the network-wide `AllowSameTokenName` dynamic parameter is `0` (the legacy/default mode), the token `name` must be globally unique, and uniqueness is enforced only at `validate()` time by checking whether the name already exists in `AssetIssueStore` [1](#0-0) . This mirrors the reported Aave Lens `createProfile` handle-uniqueness bug: any unprivileged party watching the mempool can see a pending `AssetIssueContract` with a desirable name, submit their own transaction with the same name and a higher fee/priority, and get it mined first, permanently taking the name for themselves (since token names are unique keys in `AssetIssueStore`) [2](#0-1) .

### Finding Description
Token creation is a fully public, unprivileged action: any account with enough TRX balance to cover the `AssetIssueFee` can call `createAssetIssue` [3](#0-2) . The name-uniqueness check performed in `validate()` reads the current state of `AssetIssueStore` at execution time, not at the moment the transaction was created and broadcast [1](#0-0) . Because pending transactions are visible in the transaction pool/mempool before being packed into a block, an attacker can observe a victim's pending `AssetIssueContract` transaction containing a valuable/branded token `name`, and submit a competing transaction with the identical name (with higher energy/bandwidth priority or via a well-connected node) so that it is confirmed first. Once the attacker's transaction lands, the name is claimed in `AssetIssueStore`, and the victim's original transaction will fail validation with `"Token exists"` [1](#0-0) . The attacker can then demand a ransom from the original creator to transfer/relinquish the name, exactly as described in the Aave Lens handle-squatting report.

Note: this behavior is gated by the `AllowSameTokenName` dynamic parameter — when it is set to `1` (non-zero), token names are no longer required to be unique and the name-based `"Token exists"` check is skipped [4](#0-3) . I was unable to conclusively determine, from the indexed code alone, whether `AllowSameTokenName` is presently forced to `1` on mainnet (i.e., whether this legacy uniqueness path is still reachable in production) — the default value is set via chain parameter/proposal (`ProposalUtil.java`, `DynamicPropertiesStore`) and I could not locate the current default/genesis value in the indexed files. If `AllowSameTokenName` is permanently `1` on the live network, this specific frontrunning vector for asset names is not exploitable; if it is still `0` (or can be toggled back to `0` via governance proposal), the vulnerability is live.

### Impact Explanation
If exploitable, this allows an unprivileged attacker to squat on any pending, not-yet-confirmed token name, blocking the legitimate creator from ever issuing that name and enabling extortion/ransom demands — a concrete "denial of intended state" / front-running impact similar to the original finding, though it does not directly compromise funds custody or double-spend accounting.

### Likelihood Explanation
Likelihood depends entirely on whether the legacy same-token-name-disallowed mode (`AllowSameTokenName == 0`) is still active or re-activatable on the java-tron mainnet. This is a chain-wide governable dynamic parameter, not something an application-level attacker controls, and I could not confirm its current live value from the indexed code, so likelihood is uncertain/low-to-medium pending confirmation.

### Recommendation
If the legacy unique-name mode remains reachable, either (a) permanently deprecate/disable `AllowSameTokenName == 0` mode on mainnet so names are never treated as unique reservations, or (b) if uniqueness is still desired, introduce a commit-reveal scheme for token name registration (commit a hash of name+salt first, reveal later) to remove the frontrunning window, consistent with the original report's recommended mitigation.

### Proof of Concept
1. Victim broadcasts `AssetIssueContract` tx with `name = "GOLD"` while `AllowSameTokenName == 0`.
2. Attacker observes the pending transaction in the mempool, crafts an identical `AssetIssueContract` with `name = "GOLD"`, and submits it with equal/higher fee so it gets included in an earlier or same block first.
3. Attacker's transaction executes `AssetIssueActuator.execute`, calling `assetIssueStore.put(assetIssueCapsule.createDbKey(), assetIssueCapsule)` for `"GOLD"` [5](#0-4) .
4. Victim's transaction is then processed; `validate()` finds `assetIssueStore.get("GOLD".getBytes()) != null` and throws `ContractValidateException("Token exists")` [1](#0-0) , causing the victim's transaction to fail while paying transaction fees, and leaving the name in the attacker's control.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L78-87)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L88-94)
```java

      adjustBalance(accountStore, ownerAddress, -fee);
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);//send to blackhole
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L169-174)
```java
    if (dynamicStore.getAllowSameTokenName() != 0) {
      String name = assetIssueContract.getName().toStringUtf8().toLowerCase(Locale.ROOT);
      if (("trx").equals(name)) {
        throw new ContractValidateException("assetName can't be trx");
      }
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
