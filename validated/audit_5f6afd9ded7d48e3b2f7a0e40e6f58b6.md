[1](#0-0) [2](#0-1)

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L1-1)
```java
package org.tron.core.services.jsonrpc;
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/FilterResult.java (L15-21)
```java
  public void updateExpireTime() {
    expireTimeStamp = System.currentTimeMillis() + TronJsonRpcImpl.EXPIRE_SECONDS * 1000;
  }

  public boolean isExpire() {
    return expireTimeStamp < System.currentTimeMillis();
  }
```
