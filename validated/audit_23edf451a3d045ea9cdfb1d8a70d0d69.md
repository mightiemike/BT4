### Title
Precision range validation is bypassed when `AllowSameTokenName==0`, allowing negative precision to be persisted in the legacy `AssetIssueStore` - (File: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`)

### Summary
`AssetIssueActuator.validate()` only enforces the `[0, PRECISION_DECIMAL]` range on `precision` when `dynamicStore.getAllowSameTokenName() != 0`, so any account issuing a token while this chain parameter is `0` can submit a negative (or otherwise out-of-range) `precision` value that skips validation entirely. `execute()` then persists that unsanitized value directly into the legacy `AssetIssueStore` record via the raw `AssetIssueCapsule`, while only the V2 capsule stored in `AssetIssueV2Store` is defensively reset to `0`.

### Finding Description
The validation logic is: [1](#0-0) 
The outer condition requires `getAllowSameTokenName() != 0` before the range check `(precision < 0 || precision > ActuatorConstant.PRECISION_DECIMAL)` even runs. When the chain-level `AllowSameTokenName` parameter is `0` (its default/pre-fork value), an attacker-supplied `precision` such as `-1` never gets range-checked, so `validate()` returns successfully.

In `execute()`, the code special-cases the `AllowSameTokenName == 0` branch: [2](#0-1) 
Only `assetIssueCapsuleV2` has its precision force-reset to `0` (`assetIssueCapsuleV2.setPrecision(0)`) before being written to `assetIssueV2Store`. The `assetIssueCapsule` object — which wraps the raw, unpacked `AssetIssueContract` unmodified (see `AssetIssueCapsule(AssetIssueContract)` constructor and `getPrecision()`/`setPrecision()` accessors) — is written to `assetIssueStore` (the legacy V1 store, keyed by name) with the original, unsanitized negative precision still intact: [3](#0-2) [4](#0-3) 

As a result, the persisted legacy `AssetIssueContract` record in `AssetIssueStore` contains `precision = -1` (or any out-of-range value), because nothing in the `AllowSameTokenName == 0` execute path ever sanitizes the V1 capsule's precision field the way it does for the V2 capsule.

### Impact Explanation
The malformed record is queryable through any code path that reads `AssetIssueStore` directly (e.g., legacy `GetAssetIssueByName`/`GetAssetIssueList` HTTP and gRPC endpoints, and any internal logic keyed by `createDbKeyFinal()` while `AllowSameTokenName` remains `0`), returning a raw `AssetIssueContract` with a negative `precision` to any unprivileged caller. Any downstream logic that treats `precision` as a non-negative decimal scaling factor (decimal-place computation, JSON-RPC decimals reporting, TRC10-related precompile logic) could misinterpret or mishandle this negative value, producing incorrect decimal conversion or undefined behavior — a persisted invalid state reachable and observable by any unprivileged network participant.

### Likelihood Explanation
This is trivially reachable: the only precondition is that the chain-wide `AllowSameTokenName` dynamic parameter is `0` (its historical/default value on chains/testnets that haven't enabled the multi-name-asset fork). Any account with sufficient balance to pay the standard asset-issuance fee can submit an `AssetIssueContract` with `precision = -1` and have it validated and persisted without rejection, 100% repeatable under that precondition.

### Recommendation
Move the precision range check outside the `AllowSameTokenName != 0` guard so it is applied unconditionally:
```java
int precision = assetIssueContract.getPrecision();
if (precision != 0 && (precision < 0 || precision > ActuatorConstant.PRECISION_DECIMAL)) {
  throw new ContractValidateException("precision cannot exceed 6");
}
```
Additionally, for defense in depth, sanitize/reject (rather than silently overwrite) precision on the V1 capsule path in `execute()` so stored legacy records can never diverge from validated values.

### Proof of Concept
Java unit test (extending the pattern in `framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorTest.java`):
```java
@Test
public void testNegativePrecisionBypassedWhenAllowSameTokenNameZero() {
  dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(0);

  AssetIssueContract contract = AssetIssueContract.newBuilder()
      .setOwnerAddress(ByteString.copyFrom(ownerAddress))
      .setName(ByteString.copyFromUtf8("negprec"))
      .setTotalSupply(1000L)
      .setTrxNum(1)
      .setNum(1)
      .setPrecision(-1)          // out-of-range, negative
      .setStartTime(startTime)
      .setEndTime(endTime)
      .build();

  AssetIssueActuator actuator = new AssetIssueActuator();
  actuator.setChainBaseManager(dbManager.getChainBaseManager());
  actuator.setAny(Any.pack(contract));

  // Expect validate() to THROW because precision is out of [0,6],
  // but with the current code it succeeds:
  actuator.validate(); // does NOT throw -- demonstrates the bypass

  TransactionResultCapsule ret = new TransactionResultCapsule();
  actuator.execute(ret);

  AssetIssueCapsule stored = dbManager.getAssetIssueStore().get("negprec".getBytes());
  assertEquals(-1, stored.getPrecision()); // malformed state persisted in V1 store
}
```
Fuzz/invariant extension: iterate `precision` over `{-10..-1, 7..100}` with `AllowSameTokenName` set to both `0` and `1`, and assert `validate()` throws `ContractValidateException` in all out-of-range cases regardless of the `AllowSameTokenName` flag.

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

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L176-181)
```java
    int precision = assetIssueContract.getPrecision();
    if (precision != 0
        && dynamicStore.getAllowSameTokenName() != 0
        && (precision < 0 || precision > ActuatorConstant.PRECISION_DECIMAL)) {
      throw new ContractValidateException("precision cannot exceed 6");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java (L43-45)
```java
  public AssetIssueCapsule(AssetIssueContract assetIssueContract) {
    this.assetIssueContract = assetIssueContract;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java (L87-95)
```java
  public int getPrecision() {
    return this.assetIssueContract.getPrecision();
  }

  public void setPrecision(int precision) {
    this.assetIssueContract = this.assetIssueContract.toBuilder()
        .setPrecision(precision)
        .build();
  }
```
