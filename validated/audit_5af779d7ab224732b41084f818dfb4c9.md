[1](#0-0)

### Citations

**File:** common/src/main/java/org/tron/common/utils/ByteArray.java (L179-183)
```java
  public static byte[] subArray(byte[] input, int start, int end) {
    byte[] result = new byte[end - start];
    System.arraycopy(input, start, result, 0, end - start);
    return result;
  }
```
