### Title
Improper decimal-separator handling in HTTP API numeric field parsing causes silent value inflation for `call_value`/`fee_limit`/`token_id` in transaction-creation endpoints - (File: `common/src/main/java/org/tron/json/TypeUtils.java`)

### Summary
`Util.getJsonLongValue`, used by unauthenticated HTTP API servlets (`TriggerSmartContractServlet`, `DeployContractServlet`) to read numeric fields such as `call_value`, `call_token_value`, `token_id`, `fee_limit`, `consume_user_resource_percent`, and `origin_energy_limit` from a client-supplied JSON transaction request, silently strips commas from numeric strings before parsing them as a `BigDecimal`. This mirrors the exact bug class in the external report: a value intended to use a comma as a decimal separator (e.g. `"1,25"`) is silently reinterpreted as an integer (`"125"`), inflating the parsed amount 100x, with no validation error or user confirmation step.

### Finding Description
`Util.getJsonLongValue` reads the target key via `jsonObject.getBigDecimal(key)`: [1](#0-0) 

`JSONObject.getBigDecimal` delegates to `TypeUtils.castToBigDecimal`: [2](#0-1) 

`TypeUtils.castToBigDecimal` explicitly strips all commas from the string representation of the value before constructing the `BigDecimal`, with no regard for locale/decimal-separator semantics: [3](#0-2) 

This same comma-stripping logic is also present in `castToInt` and `castToLong`: [4](#0-3) 

The unit test file explicitly documents and locks in this behavior as intentional ("Fastjson compat"), preserving the exact defect class described in the report — comma is treated purely as a thousands-separator to be discarded, never as a decimal separator: [5](#0-4) [6](#0-5) 

This parsing path is directly reachable from unauthenticated `/wallet/triggersmartcontract` and `/wallet/deploycontract` HTTP requests, where `call_value`, `call_token_value`, `token_id`, and `fee_limit` are read via `Util.getJsonLongValue` and used to build a real, signable transaction: [7](#0-6) [8](#0-7) [9](#0-8) 

If a wallet/client tool constructs `call_value` (or `fee_limit`) as a JSON string using a locale where `,` is the decimal separator (e.g., `"1,25"` intending 1.25 SUN-denominated value, or any value where a user copy-pastes a comma-formatted number), the comma is silently discarded rather than causing a parse error, producing a value 100x (or more) larger than intended with no validation failure, warning, or reconfirmation step — precisely analogous to the `ofTezString`/`removeCommas` defect in the external report.

### Impact Explanation
Because `call_value`, `fee_limit`, and `token_id` flow directly into the constructed `Transaction` returned for signing, an inflated `call_value` could cause a much larger TRX transfer than the user intended when the smart-contract call executes, and an inflated `fee_limit` could cause excessive TRX to be burned as energy fees. This is an accounting-corruption-class defect reachable via the public HTTP transaction-construction API — no privileged access, leaked key, or malicious peer is required; any client sending a comma-formatted numeric string to these endpoints is affected.

### Likelihood Explanation
Likelihood is moderate: it requires a client (wallet software, exchange integration, or a user manually crafting the JSON body) to submit a numeric field as a string containing a comma as a decimal separator. This is plausible for third-party integrations built by developers in comma-decimal locales that pass user input through with minimal sanitization, or for API consumers that naively serialize locale-formatted numbers. It does not require any protocol-level or cryptographic exploit — only that the string reach `TypeUtils.castToBigDecimal`/`castToLong`/`castToInt`.

### Recommendation
- Remove blanket comma-stripping in `TypeUtils.castToInt`, `castToLong`, and `castToBigDecimal` (`common/src/main/java/org/tron/json/TypeUtils.java`), or restrict it to strict thousands-separator grouping patterns (e.g., validate `\d{1,3}(,\d{3})*(\.\d+)?` before stripping) so a comma before a plausible fractional-length suffix is rejected rather than silently discarded.
- In `Util.getJsonLongValue` (`framework/src/main/java/org/tron/core/services/http/Util.java`), reject/replace ambiguous numeric strings (containing both a comma and digits that could represent a fractional part) with a clear `InvalidParameterException` instead of silently normalizing them.
- Require `call_value`, `fee_limit`, `token_id`, etc. to be submitted as JSON numeric literals (not strings) where feasible, since JSON numeric literals cannot contain locale-specific separators at all.

### Proof of Concept
Send an HTTP POST to `/wallet/triggersmartcontract` with a JSON body where `call_value` is submitted as a comma-containing string, e.g.:
```json
{
  "owner_address": "<hex address>",
  "contract_address": "<hex address>",
  "function_selector": "someFunction()",
  "call_value": "1,25"
}
```
`Util.getJsonLongValue(jsonObject, "call_value")` → `jsonObject.getBigDecimal("call_value")` → `TypeUtils.castToBigDecimal("1,25")` strips the comma, yielding `BigDecimal("125")`, so `build.setCallValue(125)` is set instead of the intended `1.25`-equivalent value, silently inflating the transferred TRX call value by 100x with no error surfaced to the caller.

Note: I was not able to fully verify within the remaining iterations whether every HTTP servlet consuming `Util.getJsonLongValue` (beyond `TriggerSmartContractServlet` and `DeployContractServlet`) is similarly affected, nor whether the protobuf-native `JsonFormat.merge` path (used by `TransferServlet`/`TransferAssetServlet` for the `amount` field) shares this comma-stripping defect — this would require further inspection of `JsonFormat.java`'s numeric literal parsing.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/Util.java (L507-513)
```java
  public static long getJsonLongValue(JSONObject jsonObject, String key, boolean required) {
    BigDecimal bigDecimal = jsonObject.getBigDecimal(key);
    if (required && bigDecimal == null) {
      throw new InvalidParameterException("key [" + key + "] does not exist");
    }
    return (bigDecimal == null) ? 0L : bigDecimal.longValueExact();
  }
```

**File:** common/src/main/java/org/tron/json/JSONObject.java (L104-106)
```java
  public BigDecimal getBigDecimal(String key) {
    return TypeUtils.castToBigDecimal(get(key));
  }
```

**File:** common/src/main/java/org/tron/json/TypeUtils.java (L89-141)
```java
    if (value instanceof String) {
      String strVal = (String) value;
      if (strVal.isEmpty()
          || "null".equals(strVal)
          || "NULL".equals(strVal)) {
        return null;
      }
      if (strVal.indexOf(',') != -1) {
        strVal = strVal.replaceAll(",", "");
      }
      strVal = NUMBER_WITH_TRAILING_ZEROS_PATTERN.matcher(strVal).replaceAll("");
      return Integer.parseInt(strVal);
    }

    if (value instanceof Boolean) {
      return (Boolean) value ? 1 : 0;
    }

    throw new JSONException("can not cast to int, value : " + value);
  }

  static Long castToLong(Object value) {
    if (value == null) {
      return null;
    }

    if (value instanceof BigDecimal) {
      return longValue((BigDecimal) value);
    }

    if (value instanceof Number) {
      return ((Number) value).longValue();
    }

    if (value instanceof String) {
      String strVal = (String) value;
      if (strVal.isEmpty()
          || "null".equals(strVal)
          || "NULL".equals(strVal)) {
        return null;
      }
      if (strVal.indexOf(',') != -1) {
        strVal = strVal.replaceAll(",", "");
      }
      try {
        return Long.parseLong(strVal);
      } catch (NumberFormatException ex) {
        // Fastjson falls through to BigDecimal attempt
      }

      strVal = NUMBER_WITH_TRAILING_ZEROS_PATTERN.matcher(strVal).replaceAll("");
      return Long.parseLong(strVal);
    }
```

**File:** common/src/main/java/org/tron/json/TypeUtils.java (L169-184)
```java
    String strVal = value.toString();

    if (strVal.isEmpty() || "null".equalsIgnoreCase(strVal)) {
      return null;
    }

    if (strVal.length() > 65535) {
      throw new JSONException("decimal overflow");
    }

    if (strVal.indexOf(',') != -1) {
      strVal = strVal.replaceAll(",", "");
    }

    return new BigDecimal(strVal);
  }
```

**File:** framework/src/test/java/org/tron/json/JsonTest.java (L356-360)
```java
    // numeric string — comma stripping + trailing-zero stripping (Fastjson compat)
    assertEquals(Integer.valueOf(1000), TypeUtils.castToInt("1,000"));
    assertEquals(Integer.valueOf(1), TypeUtils.castToInt("1.0"));
    assertEquals(Long.valueOf(2_000L), TypeUtils.castToLong("2,000"));
    assertEquals(Long.valueOf(9_000_000_000L), TypeUtils.castToLong("9000000000"));
```

**File:** framework/src/test/java/org/tron/json/JsonTest.java (L377-377)
```java
    assertEquals(new BigDecimal("1000.5"), TypeUtils.castToBigDecimal("1,000.5"));
```

**File:** framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java (L70-79)
```java
      build.setCallTokenValue(Util.getJsonLongValue(jsonObject, "call_token_value"));
      build.setTokenId(Util.getJsonLongValue(jsonObject, "token_id"));
      build.setCallValue(Util.getJsonLongValue(jsonObject, "call_value"));
      long feeLimit = Util.getJsonLongValue(jsonObject, "fee_limit");
      TransactionCapsule trxCap = wallet
          .createTransactionCapsule(build.build(), ContractType.TriggerSmartContract);

      Transaction.Builder txBuilder = trxCap.getInstance().toBuilder();
      Transaction.raw.Builder rawBuilder = trxCap.getInstance().getRawData().toBuilder();
      rawBuilder.setFeeLimit(feeLimit);
```

**File:** framework/src/main/java/org/tron/core/services/http/DeployContractServlet.java (L45-62)
```java
      build.setCallTokenValue(Util.getJsonLongValue(jsonObject, "call_token_value"))
          .setTokenId(Util.getJsonLongValue(jsonObject, "token_id"));
      ABI.Builder abiBuilder = ABI.newBuilder();
      if (jsonObject.containsKey("abi")) {
        String abi = jsonObject.getString("abi");
        StringBuffer abiSB = new StringBuffer("{");
        abiSB.append("\"entrys\":");
        abiSB.append(abi);
        abiSB.append("}");
        JsonFormat.merge(abiSB.toString(), abiBuilder, params.isVisible());
      }
      SmartContract.Builder smartBuilder = SmartContract.newBuilder();
      smartBuilder
          .setAbi(abiBuilder)
          .setCallValue(Util.getJsonLongValue(jsonObject, "call_value"))
          .setConsumeUserResourcePercent(Util.getJsonLongValue(jsonObject,
              "consume_user_resource_percent"))
          .setOriginEnergyLimit(Util.getJsonLongValue(jsonObject, "origin_energy_limit"));
```

**File:** framework/src/main/java/org/tron/core/services/http/DeployContractServlet.java (L80-80)
```java
      long feeLimit = Util.getJsonLongValue(jsonObject, "fee_limit");
```
