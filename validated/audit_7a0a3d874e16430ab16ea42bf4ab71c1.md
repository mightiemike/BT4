No vulnerability found for this question.

**Analysis**: `Util.getHexAddress` calls `Commons.decodeFromBase58Check`, which is a deterministic pure function: for any given base58check string it performs a fixed-length check (`BASE58_ADDRESS_LENGTH == 34`), decodes via `Base58.decode`, verifies the SHA256d checksum, and validates the address prefix byte via `DecodeUtil.addressValid` before returning bytes. [1](#0-0) 

This means `Util.getHexAddress(X)` always returns the exact same hex string for the same base58 input `X`, with no ambiguity or collision path in the decode logic. [2](#0-1) 

In `GetContractServlet.doGet`, `Util.getVisible` reads the `visible` flag and, if true, converts the `value` parameter from base58check to hex via `Util.getHexAddress` before building the `BytesMessage` passed to `wallet.getContract`. [3](#0-2) 

The premise in the question — that request A (`visible=true`, base58 `X`) and request B (`visible=false`, raw hex `Y`) could return different `SmartContract` payloads "for the identical underlying account" when `Y != decode(X)` — is not a bug. If `Y != decode(X)`, then `Y` and `X` are simply different addresses by definition; they do not represent "the same logical address," so returning different results is correct, expected behavior, not an encoding-confusion vulnerability. There is no code path where a single base58check string decodes to two different byte arrays, nor where `visible=true` and `visible=false` paths diverge for the same underlying bytes — both paths converge on the same hex string before being passed to `wallet.getContract`. The existing test `GetContractServletTest` confirms this: both GET and POST paths resolve to the identical `BytesMessage` for the same address input. [4](#0-3) 

There is no reachable path for an unprivileged attacker to cause "the same logical address" to resolve to two different raw byte arrays; this is purely a matter of the attacker deliberately supplying two different (non-corresponding) addresses in the two requests, which is not a vulnerability in the codebase.

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/Commons.java (L49-68)
```java
  public static byte[] decodeFromBase58Check(String addressBase58) {
    if (StringUtils.isEmpty(addressBase58)) {
      logger.debug("address is empty !!");
      return null;
    }
    if (addressBase58.length() != BASE58_ADDRESS_LENGTH) {
      logger.debug("invalid Base58 address length");
      return null;
    }
    byte[] address = decode58Check(addressBase58);
    if (address == null) {
      return null;
    }

    if (!DecodeUtil.addressValid(address)) {
      return null;
    }

    return address;
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/Util.java (L421-428)
```java
  public static String getHexAddress(final String address) {
    if (address != null) {
      byte[] addressByte = decodeFromBase58Check(address);
      return ByteArray.toHexString(addressByte);
    } else {
      return null;
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetContractServlet.java (L23-35)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String input = request.getParameter(S_VALUE);
      if (visible) {
        input = Util.getHexAddress(input);
      }

      JSONObject jsonObject = new JSONObject();
      jsonObject.put(S_VALUE, input);
      BytesMessage.Builder build = BytesMessage.newBuilder();
      JsonFormat.merge(jsonObject.toJSONString(), build, visible);
      SmartContract smartContract = wallet.getContract(build.build());
```

**File:** framework/src/test/java/org/tron/core/services/http/GetContractServletTest.java (L20-24)
```java
  private final byte[] address = new ECKey().getAddress();
  private final String addrStr = ByteArray.toHexString(address);
  private final GrpcAPI.BytesMessage expectedRequest = GrpcAPI.BytesMessage.newBuilder()
      .setValue(ByteString.copyFrom(address))
      .build();
```
