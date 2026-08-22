[1](#0-0)

### Citations

**File:** crypto/src/main/java/org/tron/common/crypto/Rsv.java (L17-25)
```java
  public static Rsv fromSignature(byte[] sign) {
    byte[] r = Arrays.copyOfRange(sign, 0, 32);
    byte[] s = Arrays.copyOfRange(sign, 32, 64);
    byte v = sign[64];
    if (v < 27) {
      v += 27; //revId -> v
    }
    return new Rsv(r, s, v);
  }
```
