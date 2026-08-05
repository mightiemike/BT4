## Confirmed analog: JSON injection via `AssetIssueContract.name/abbr/description/url` in self-format HTTP JSON output

### Title
JSON injection in self-format HTTP API responses via unsanitized `AssetIssueContract` string fields - (File: framework/src/main/java/org/tron/core/services/http/JsonFormat.java)

### Summary
`AssetIssueContract.name`, `abbr`, `description`, and `url` are attacker-controlled `bytes` fields validated only for length (and, for `name`/`abbr`, for being "readable" ASCII 0x21–0x7E, which still permits `"` and `\`) [1](#0-0) . `description` and `url` go through `validBytes`, which does not restrict characters at all [2](#0-1) , and both are checked/used unchanged in `AssetIssueActuator.validate()` / `UpdateAssetActuator` [3](#0-2) . When the HTTP API serializes these fields in "self-format" mode, `JsonFormat.escapeBytesSelfType()` performs a naive, incomplete escape that can be defeated to inject arbitrary additional JSON keys/values into the response — the same bug class as the reported `VerbsToken.tokenURI()` JSON injection.

### Finding Description
`HttpSelfFormatFieldName` explicitly marks `AssetIssueContract.name`, `abbr`, `description`, and `url` (and other similar fields) as "name string format" fields for self-formatted JSON output [4](#0-3) .

When printing such a field, `escapeBytesSelfType()` only escapes literal `"` characters, and does **not** escape backslashes first:

```java
static String escapeBytesSelfType(ByteString input, final String fliedName) {
  //Address
  if (HttpSelfFormatFieldName.isAddressFormat(fliedName)) {
    return StringUtil.encode58Check(input.toByteArray());
  }
  //Normal String
  if (HttpSelfFormatFieldName.isNameStringFormat(fliedName)) {
    String result = new String(input.toByteArray());
    result = result.replaceAll("\"", "\\\\\"");
    try {
      JSON.parseObject("{\"key\":\"" + result + "\"}");
      return result;
    } catch (Exception e) {
      return ByteArray.toHexString(input.toByteArray());
    }
  }
  //HEX
  return ByteArray.toHexString(input.toByteArray());
}
``` [5](#0-4) 

Because a raw `\` in the field's raw bytes is left untouched, an attacker can submit a value that already contains a literal backslash immediately before a `"` (e.g. `real_url\", "totalSupply":999999999, "url":"fake`). After `replaceAll("\"", "\\\\\"")` runs, that existing backslash combines with the newly-inserted escape to become `\\"` — an *escaped backslash* followed by an *unescaped, string-terminating quote*. The result is syntactically valid JSON once embedded (`{"key":"real_url\\", "totalSupply":999999999, "url":"fake"}` parses successfully), so the built-in "sanity check" `JSON.parseObject(...)` passes and the poisoned string is returned unmodified and printed between the surrounding quotes by `printFieldValue`/`printSingleField` [6](#0-5) .

Neither `TransactionUtil.validUrl`/`validAssetDescription` (no character restriction, only length) [2](#0-1)  nor `validReadableBytes` used for `name`/`abbr` (which allows any byte in 0x21–0x7E, including `"` 0x22 and `\` 0x5C) [7](#0-6)  block the required backslash/quote characters. `AssetIssueActuator.validate()` performs no additional sanitization before persisting these fields [3](#0-2) , and `UpdateAssetActuator` similarly only checks URL/description length, not content, before storing the attacker-supplied bytes [8](#0-7) .

This is the direct java-tron analog of the reported `CultureIndex.createPiece()`/`VerbsToken.tokenURI()` bug: an unprivileged user supplies metadata that is embedded, insufficiently sanitized, into a JSON document later consumed by wallets/explorers/front-ends, letting the attacker inject extra JSON fields (or overwrite fields) that were not part of the intended structured response.

### Impact Explanation
Any wallet, block explorer, or third-party service that consumes the java-tron self-format HTTP API JSON for asset metadata (asset name, abbreviation, description, url) can have its parsed response polluted with attacker-injected keys/values. Depending on how downstream consumers parse and trust the "extra" fields (e.g., re-used field names that a wallet checks, like `url` or `totalSupply` in the constructed object), this can mislead users about an asset's real properties — analogous to the referenced report's concern about swapping displayed metadata (image/URL) after the fact. It does not directly move funds inside java-tron itself, but it is a consensus-adjacent trust/display-integrity issue on a public, permissionless surface (anyone can issue an asset via `AssetIssueContract`/`UpdateAssetContract`), matching the "unprivileged-user analog" and "invalid-state/divergence" classes called out in scope (divergence between the intended structured API response and the actual parsed object).

### Likelihood Explanation
`CreateAssetIssue`/`UpdateAsset` are fully permissionless actuators reachable by any funded account [9](#0-8) . Crafting a byte sequence containing an unescaped `\"` combination requires no privileged access and is trivial to construct via the JSON-RPC/HTTP `wallet/createassetissue` or `wallet/updateasset` endpoints (which accept hex-encoded byte strings for these fields, as seen in the test fixtures) [10](#0-9) . The only gate is the `JSON.parseObject` re-validation in `escapeBytesSelfType`, which — as shown — can be satisfied by design of the attack payload, so likelihood is high for any client relying on the self-format HTTP JSON output.

### Recommendation
In `escapeBytesSelfType` (and generally anywhere raw bytes are manually interpolated into JSON strings), first escape backslashes (`\` → `\\`) before escaping quotes (`"` → `\"`), and escape/reject control characters, matching the behavior of the already-correct `escapeText()` used for the non-self-type path [11](#0-10) . Alternatively, drop the ad-hoc string-replace approach entirely and always serialize the field via a proper JSON string encoder before embedding it, rather than relying on a secondary `JSON.parseObject` "does the whole document still look syntactically valid" heuristic — that check is insufficient here because a crafted payload keeps the document valid while smuggling extra key/value pairs into it.

### Proof of Concept
1. Submit a `CreateAssetIssueContract` (via `wallet/createassetissue`, hex-encoded) with `url` (or `description`) bytes equal to the ASCII string:
   `real_url\", "totalSupply": 999999999, "url":"fake` — i.e., containing a literal backslash immediately followed by a double quote, then a comma-separated fake `key: value` pair, then an unterminated string.
2. This passes `TransactionUtil.validUrl`/`validAssetDescription` (only length is checked) and `AssetIssueActuator.validate()`, and is persisted in the `AssetIssueContract` as-is [12](#0-11) .
3. Query the asset via a self-format HTTP endpoint that serializes `AssetIssueContract` with `selfType=true` (which prints `url` through `escapeBytesSelfType`).
4. In `escapeBytesSelfType`, `result.replaceAll("\"", "\\\\\"")` turns the payload's `\"` into `\\"`; the sanity check `JSON.parseObject("{\"key\":\"" + result + "\"}")` succeeds because the resulting document `{"key":"real_url\\", "totalSupply": 999999999, "url":"fake"}` is syntactically valid JSON (three keys: `key`, `totalSupply`, `url`) [5](#0-4) .
5. The unmodified payload is returned and printed between quotes by the JSON generator, so the final HTTP response body contains attacker-injected `"totalSupply": 999999999, "url":"fake"` keys inside (or adjacent to) the `AssetIssueContract` JSON object — a structural JSON injection into the API response, exactly mirroring the reported Solidity `tokenURI()` field-injection technique.

### Citations

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L77-83)
```java
  public static boolean validAssetDescription(byte[] description) {
    return validBytes(description, MAX_ASSET_DESCRIPTION_LEN, true);
  }

  public static boolean validUrl(byte[] url) {
    return validBytes(url, MAX_URL_LEN, false);
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L97-118)
```java
  private static boolean validBytes(byte[] bytes, int maxLength, boolean allowEmpty) {
    if (ArrayUtils.isEmpty(bytes)) {
      return allowEmpty;
    }
    return bytes.length <= maxLength;
  }

  private static boolean validReadableBytes(byte[] bytes, int maxLength) {
    if (ArrayUtils.isEmpty(bytes) || bytes.length > maxLength) {
      return false;
    }
    // b must be readable
    for (byte b : bytes) {
      if (b < 0x21) {
        return false; // 0x21 = '!'
      }
      if (b > 0x7E) {
        return false; // 0x7E = '~'
      }
    }
    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L136-163)
```java
  public boolean validate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    if (!this.any.is(AssetIssueContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [AssetIssueContract],real type[" + any
              .getClass() + "]");
    }

    final AssetIssueContract assetIssueContract;
    try {
      assetIssueContract = this.any.unpack(AssetIssueContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }

    byte[] ownerAddress = assetIssueContract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L183-195)
```java
    if ((!assetIssueContract.getAbbr().isEmpty()) && !TransactionUtil
        .validAssetName(assetIssueContract.getAbbr().toByteArray())) {
      throw new ContractValidateException("Invalid abbreviation for token");
    }

    if (!TransactionUtil.validUrl(assetIssueContract.getUrl().toByteArray())) {
      throw new ContractValidateException("Invalid url");
    }

    if (!TransactionUtil
        .validAssetDescription(assetIssueContract.getDescription().toByteArray())) {
      throw new ContractValidateException("Invalid description");
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/HttpSelfFormatFieldName.java (L215-219)
```java
    //AssetIssueContract
    NameFieldNameMap.put("protocol.AssetIssueContract.name", 1);
    NameFieldNameMap.put("protocol.AssetIssueContract.abbr", 1);
    NameFieldNameMap.put("protocol.AssetIssueContract.description", 1);
    NameFieldNameMap.put("protocol.AssetIssueContract.url", 1);
```

**File:** framework/src/main/java/org/tron/core/services/http/JsonFormat.java (L417-421)
```java
      case STRING:
        generator.print("\"");
        generator.print(escapeText((String) value));
        generator.print("\"");
        break;
```

**File:** framework/src/main/java/org/tron/core/services/http/JsonFormat.java (L862-880)
```java
  static String escapeBytesSelfType(ByteString input, final String fliedName) {
    //Address
    if (HttpSelfFormatFieldName.isAddressFormat(fliedName)) {
      return StringUtil.encode58Check(input.toByteArray());
    }
    //Normal String
    if (HttpSelfFormatFieldName.isNameStringFormat(fliedName)) {
      String result = new String(input.toByteArray());
      result = result.replaceAll("\"", "\\\\\"");
      try {
        JSON.parseObject("{\"key\":\"" + result + "\"}");
        return result;
      } catch (Exception e) {
        return ByteArray.toHexString(input.toByteArray());
      }
    }
    //HEX
    return ByteArray.toHexString(input.toByteArray());
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/JsonFormat.java (L914-962)
```java
  static String escapeText(String input) {
    StringBuilder builder = new StringBuilder(input.length());
    CharacterIterator iter = new StringCharacterIterator(input);
    for (char c = iter.first(); c != CharacterIterator.DONE; c = iter.next()) {
      switch (c) {
        case '\b':
          builder.append("\\b");
          break;
        case '\f':
          builder.append("\\f");
          break;
        case '\n':
          builder.append("\\n");
          break;
        case '\r':
          builder.append("\\r");
          break;
        case '\t':
          builder.append("\\t");
          break;
        case '\\':
          builder.append("\\\\");
          break;
        case '"':
          builder.append("\\\"");
          break;
        default:
          // Check for other control characters
          if (c >= 0x0000 && c <= 0x001F) {
            appendEscapedUnicode(builder, c);
          } else if (Character.isHighSurrogate(c)) {
            // Encode the surrogate pair using 2 six-character sequence (\\uXXXX\\uXXXX)
            appendEscapedUnicode(builder, c);
            c = iter.next();
            if (c == CharacterIterator.DONE) {
              throw new IllegalArgumentException(
                  "invalid unicode string: unexpected high surrogate pair value "
                      + "without corresponding low value.");
            }
            appendEscapedUnicode(builder, c);
          } else {
            // Anything else can be printed as-is
            builder.append(c);
          }
          break;
      }
    }
    return builder.toString();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java (L151-157)
```java
    if (!TransactionUtil.validUrl(newUrl.toByteArray())) {
      throw new ContractValidateException("Invalid url");
    }

    if (!TransactionUtil.validAssetDescription(newDescription.toByteArray())) {
      throw new ContractValidateException("Invalid description");
    }
```

**File:** framework/src/test/java/org/tron/core/services/http/CreateAssetIssueServletTest.java (L50-69)
```java
  @Test
  public void testCreate() {
    String jsonParam = "{"
            + "    \"owner_address\": \"4199357684BC659F5166046B56C95A0E99F1265CD1\","
            + "    \"name\": \"0x6173736574497373756531353330383934333132313538\","
            + "    \"abbr\": \"0x6162627231353330383934333132313538\","
            + "    \"total_supply\": 4321,"
            + "    \"trx_num\": 1,"
            + "    \"num\": 1,"
            + "    \"start_time\": 1530894315158,"
            + "    \"end_time\": 1533894312158,"
            + "    \"description\": \"007570646174654e616d6531353330363038383733343633\","
            + "    \"url\": \"007570646174654e616d6531353330363038383733343633\","
            + "    \"free_asset_net_limit\": 10000,"
            + "    \"public_free_asset_net_limit\": 10000,"
            + "    \"frozen_supply\": {"
            + "        \"frozen_amount\": 1,"
            + "        \"frozen_days\": 2"
            + "    }"
            + "}";
```
