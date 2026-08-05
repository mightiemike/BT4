### Title
`ValidateAddressServlet.validAddress` accepts unchecksummed Base64-format addresses that no transaction-submission entrypoint recognizes - (File: `framework/src/main/java/org/tron/core/services/http/ValidateAddressServlet.java`)

### Summary
`ValidateAddressServlet.validAddress` treats any 28-character string as a "Base64 format" address, decodes it with plain `Base64.getDecoder().decode()`, and reports `result: true` as long as the decoded bytes are 21 bytes long with the correct network prefix byte. Neither `TransferServlet` nor `TriggerSmartContractServlet` (nor `JsonFormat.unescapeBytesSelfType`, which they rely on) ever accept Base64-encoded address strings — they only understand raw hex (when `visible=false`) or Base58Check (when `visible=true`, via `Commons.decodeFromBase58Check`).

### Finding Description
`ValidateAddressServlet.validAddress` dispatches on the input length: [1](#0-0) 

- length 42 → treated as hex, decoded with `ByteArray.fromHexString`
- length 34 → treated as Base58Check, decoded/validated with `Commons.decodeFromBase58Check` (which internally requires a valid checksum via `Commons.decode58Check` and `DecodeUtil.addressValid`) [2](#0-1) 
- length 28 → treated as Base64, decoded with plain `Base64.getDecoder().decode(input)` with **no checksum step at all**

For all three branches the only remaining check is `DecodeUtil.addressValid(address)`, which merely verifies the array is exactly 21 bytes and starts with the correct prefix byte: [3](#0-2) 

By contrast, the actual transaction-submission path (`TransferServlet`, `TriggerSmartContractServlet`) parses the `owner_address`/`to_address` fields through `JsonFormat.merge` → `JsonFormat.unescapeBytesSelfType`, which only supports two formats: [4](#0-3) 
- If the field is an address field and `visible=true`, it calls `Commons.decodeFromBase58Check` (Base58Check only).
- Otherwise it falls through to `unescapeBytes`, which treats the string as a raw hex-escaped byte sequence (i.e., expects a hex string), not Base64.

There is no code path in `TransferServlet` or `TriggerSmartContractServlet` that ever calls `Base64.getDecoder().decode()` on an address field. Consequently, any 28-character string that `ValidateAddressServlet` accepts as a valid "Base64 format" address (e.g., an arbitrary Base64 string that happens to decode to 21 bytes starting with `0x41`) can never actually be used as an address in a real transaction submission — it will fail to parse as hex (wrong length/characters) and fail as Base58Check (fails `Commons.decode58Check`'s checksum or is rejected outright by length checks).

This produces the exact divergence the audit describes: `validateaddress` reports `result: true` for an address string that every real transaction-processing entrypoint rejects outright (as unparseable/invalid format, not merely a validation failure).

### Impact Explanation
Any client or dApp using `/wallet/validateaddress` as a pre-flight check for address correctness before submitting a transaction can be misled for Base64-format inputs specifically: the endpoint reports a positive validation result for a string that downstream transfer/contract-trigger endpoints cannot process at all. This is a confusion/availability issue — it does not enable fund theft or unauthorized execution, but it breaks the implicit invariant that `validateaddress` is a reliable predictor of whether a downstream call with the same address string will succeed, and could be leveraged to cause failed/rejected transactions or automation logic errors in systems that trust the pre-check.

### Likelihood Explanation
This is trivially and deterministically reproducible by any unprivileged caller of the public HTTP API — no special preconditions, keys, or state are required. An attacker/tester simply needs any 21-byte value with the correct prefix byte, Base64-encode it into a 28-character string, and submit it to `/wallet/validateaddress`.

### Recommendation
Either (a) remove the Base64 branch from `ValidateAddressServlet.validAddress` since it has no corresponding accepted format in any transaction-submission entrypoint, or (b) add equivalent Base64 address support (with an explicit format/checksum scheme, not just length+prefix) to `JsonFormat.unescapeBytesSelfType` and any other address-decoding entrypoints, so that `validateaddress`'s accept/reject decision is guaranteed consistent with actual downstream parsing.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/services/http/ValidateAddressDivergenceTest.java
@Test
public void testBase64AddressAcceptedByValidateButRejectedByTransfer() throws Exception {
  // Build a 21-byte address: prefix 0x41 + 20 arbitrary bytes
  byte[] raw = new byte[21];
  raw[0] = 0x41;
  new Random(42).nextBytes(raw); // deterministic filler after prefix reset below
  raw[0] = 0x41;
  String base64Addr = Base64.getEncoder().encodeToString(raw); // length 28

  // 1. ValidateAddressServlet reports valid
  ValidateAddressServlet validateServlet = new ValidateAddressServlet();
  MockHttpServletRequest req1 = new MockHttpServletRequest();
  req1.setParameter("address", base64Addr);
  MockHttpServletResponse resp1 = new MockHttpServletResponse();
  validateServlet.doGet(req1, resp1);
  String body1 = resp1.getContentAsString();
  assertTrue(body1.contains("\"result\":true")); // passes today

  // 2. TransferServlet rejects the same string as owner_address/to_address
  TransferServlet transferServlet = new TransferServlet();
  String json = "{\"owner_address\":\"" + base64Addr + "\","
      + "\"to_address\":\"" + base64Addr + "\",\"amount\":1,\"visible\":true}";
  MockHttpServletRequest req2 = new MockHttpServletRequest();
  req2.setContent(json.getBytes());
  MockHttpServletResponse resp2 = new MockHttpServletResponse();
  transferServlet.doPost(req2, resp2);
  String body2 = resp2.getContentAsString();

  // Expected: transaction creation fails (invalid/unparseable address),
  // proving divergence from validateaddress's "true" result.
  assertTrue(body2.contains("Invalid") || body2.contains("Error"));
}
```
Expected assertions: `validateaddress` returns `result: true` for the Base64 string, while `TransferServlet` (and similarly `TriggerSmartContractServlet`) fails to parse/reject the identical string as an address, confirming the non-deterministic/divergent validation behavior across public entrypoints.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/ValidateAddressServlet.java (L26-47)
```java
      if (input.length() == DecodeUtil.ADDRESS_SIZE) {
        //hex
        address = ByteArray.fromHexString(input);
        msg = "Hex string format";
      } else if (input.length() == 34) {
        //base58check
        address = Commons.decodeFromBase58Check(input);
        msg = "Base58check format";
      } else if (input.length() == 28) {
        //base64
        address = Base64.getDecoder().decode(input);
        msg = "Base64 format";
      } else {
        result = false;
        msg = "Length error";
      }
      if (result) {
        result = DecodeUtil.addressValid(address);
        if (!result) {
          msg = "Invalid address";
        }
      }
```

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

**File:** common/src/main/java/org/tron/common/utils/DecodeUtil.java (L15-33)
```java
  public static boolean addressValid(byte[] address) {
    if (ArrayUtils.isEmpty(address)) {
      logger.warn("Warning: Address is empty !!");
      return false;
    }
    if (address.length != ADDRESS_SIZE / 2) {
      logger.warn(
          "Warning: Address length need " + ADDRESS_SIZE + " but " + address.length
              + " !!");
      return false;
    }

    if (address[0] != addressPreFixByte) {
      logger.warn("Warning: Address need prefix with " + addressPreFixByte + " but "
          + address[0] + " !!");
      return false;
    }
    return true;
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/JsonFormat.java (L1341-1365)
```java
    static ByteString unescapeBytesSelfType(String input, final String fliedName)
        throws InvalidEscapeSequence {
      //Address base58 -> ByteString
      if (HttpSelfFormatFieldName.isAddressFormat(fliedName)) {
        byte[] addressBytes = null;
        try {
          addressBytes = Commons.decodeFromBase58Check(input);
        } catch (IllegalArgumentException e) {
          // Base58.decode throws on illegal chars -> leave addressBytes null (treated as invalid)
        }
        if (addressBytes == null) {
          // empty / wrong-length / bad-checksum / illegal chars -> all invalid addresses; throw a
          // clear error instead of letting ByteString.copyFrom(null) throw a bare NPE.
          throw new InvalidEscapeSequence("invalid address for field: " + fliedName);
        }
        return ByteString.copyFrom(addressBytes);
      }

      //Normal String -> ByteString
      if (HttpSelfFormatFieldName.isNameStringFormat(fliedName)) {
        return ByteString.copyFromUtf8(input);
      }

      return unescapeBytes(input);
    }
```
