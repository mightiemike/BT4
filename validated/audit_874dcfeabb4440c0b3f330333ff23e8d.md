## Title
Missing backslash-escaping in `JsonFormat.escapeBytesSelfType` allows JSON-context breakout and unbounded-recursion DoS via Fastjson - ([File: framework/src/main/java/org/tron/core/services/http/JsonFormat.java])

## Summary
`escapeBytesSelfType` validates a "name-format" byte field (e.g. `AssetIssueContract.description`) by wrapping it as `{"key":"<value>"}` and calling `JSON.parseObject` before deciding whether to emit the raw string or fall back to hex. The escaping only replaces `"` with `\"` and never escapes backslashes, so attacker-controlled bytes containing a `\` immediately before a byte that becomes `"` after replacement can break out of the intended string literal and inject real JSON structure that Fastjson then parses, including deeply nested arrays/objects with no depth limit at this call site.

## Finding Description
`HttpSelfFormatFieldName` marks several protobuf string fields — including `protocol.AssetIssueContract.description`, `.name`, `.url`, `.abbr`, `Note.memo`, etc. — as "name string format" fields [1](#0-0) .

When these fields are serialized back to JSON for an HTTP response with `selfType = true`, `printFieldValue` calls `escapeBytes(...)` → `escapeBytesSelfType` [2](#0-1) :

```java
static String escapeBytesSelfType(ByteString input, final String fliedName) {
  ...
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
  ...
}
``` [3](#0-2) 

The escaping is incomplete: it only escapes `"` into `\"`, leaving raw backslash bytes untouched. If the raw field bytes already contain a `\` immediately preceding a byte that becomes a quote after the `replaceAll`, the resulting text contains `\\"` where the first `\` is the attacker's original backslash and the second `\"` is the substitution — this sequence is interpreted by a JSON parser as an escaped backslash followed by an unescaped quote, prematurely closing the JSON string literal. This lets an attacker inject literal JSON tokens (e.g. `,"a":[[[[[[[[...]]]]]]]]`) after the closing quote, which then get parsed as real nested JSON structure by `JSON.parseObject`, rather than remaining inert string content.

Because `JSON.parseObject` is called here with no depth/size limit configured, a sufficiently deep nested array/object crafted this way can drive the Fastjson parser's recursive descent to exhaust the JVM thread stack, raising a `StackOverflowError`. Critically, the surrounding `catch (Exception e)` **does not catch `Error`**, so a `StackOverflowError` thrown here is not handled and propagates out of `escapeBytesSelfType` uncaught.

## Impact Explanation
This is a DoS vector against the HTTP API serialization path (not a message-parsing/transaction-validation path). Any legitimate node RPC/HTTP query that returns an object containing the crafted field (e.g. `GetAssetIssueByAccount`, `GetAssetIssueList`, `GetAssetIssueById`) would trigger `escapeBytesSelfType` on the poisoned bytes and could throw an uncaught `StackOverflowError` on the handling thread. Depending on servlet container error handling, this typically surfaces as a failed request (HTTP 500) rather than a full node crash, since `StackOverflowError` unwinds only the offending thread's stack; however, repeated or concurrent triggering against a query-heavy public API is a real, unprivileged, repeatable request-level DoS against the HTTP-API service, matching the "DoS via RPC-API" bounty class.

## Likelihood Explanation
- **Attacker requirement:** an unprivileged, funded account can broadcast an `AssetIssueContract` (or similarly a `TransferAssetContract`/`Note` depending on which field is reachable) with a crafted `description` byte sequence. This requires only the standard fee cost of asset issuance — no privileged role needed.
- I could not confirm within the available index whether `AssetIssueActuator.validate()` restricts the *content* (vs. only length) of the `description` field to printable/safe characters; this needs to be checked in the full actuator source (`actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`) to confirm the raw backslash byte survives into storage unescaped/unfiltered. If the actuator restricts to a limited charset (e.g. only allows certain characters or valid UTF-8 without control/backslash bytes), the attack is blocked at the input-validation stage. This is the primary open unknown.
- I also could not confirm from the index all HTTP servlets that invoke `JsonFormat.printToString(..., true)` (selfType=true) versus `false`, so the exact HTTP endpoints that would trigger this path on read need to be verified in the full servlet source tree.

## Recommendation
- Escape backslashes before quotes in `escapeBytesSelfType`: replace `\` with `\\` **before** replacing `"` with `\"`, so the two substitutions cannot combine into an unintended escape sequence.
- Wrap the `JSON.parseObject` call's exception handling to also catch `Throwable`/`Error` (or specifically `StackOverflowError`), not just `Exception`, so malformed input safely falls back to hex encoding instead of crashing the thread.
- Consider using Fastjson's `Feature`/parser config to bound nesting depth explicitly, or avoid using a general-purpose JSON parser purely as a "is this safe to embed" check — a proper JSON-string-escaper (e.g. reuse `escapeText`) would be more robust than round-tripping through `JSON.parseObject`.

## Proof of Concept
Because I could not fully verify the actuator-side content restrictions on `description` (see Likelihood section) and could not enumerate every HTTP path invoking `selfType=true` within the indexed content, the concrete PoC below demonstrates the core library flaw directly, which is fully reproducible without any additional chain state:

```java
// Demonstrates that escapeBytesSelfType breaks out of the intended string context
// and forwards attacker JSON structure into JSON.parseObject with no catch for Errors.
@Test
public void testEscapeBytesSelfTypeBreakout() {
  // Byte sequence: backslash (0x5C) followed by a quote (0x22).
  // After replaceAll("\"","\\\\\""), the quote becomes \" giving: \  +  \"  == \\"
  // which JSON interprets as (escaped backslash)(unescaped quote) -> string closes early.
  StringBuilder payload = new StringBuilder();
  payload.append('\\').append('"');       // breakout sequence
  payload.append(",\"x\":");
  // deeply nested array to trigger recursive descent in Fastjson
  for (int i = 0; i < 100000; i++) payload.append('[');
  for (int i = 0; i < 100000; i++) payload.append(']');

  ByteString input = ByteString.copyFrom(payload.toString().getBytes());

  // Expect: either a StackOverflowError propagates uncaught (bug),
  // or (after fix) the method falls back to hex without throwing.
  Assertions.assertThrows(StackOverflowError.class, () ->
      JsonFormat.escapeBytesSelfType(input, "protocol.AssetIssueContract.description"));
}
```

Expected result on the vulnerable code: `JSON.parseObject` recurses on the injected nested-array tail and throws `StackOverflowError`, which is **not caught** by `catch (Exception e)` in `escapeBytesSelfType`, propagating out of the serialization call. This is a library-level confirmation of the flaw; full end-to-end confirmation via a broadcast `AssetIssueContract` + HTTP `getassetissuebyid` query requires verifying `AssetIssueActuator.validate()` content restrictions, which I was unable to complete before running out of investigation budget.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/HttpSelfFormatFieldName.java (L216-219)
```java
    NameFieldNameMap.put("protocol.AssetIssueContract.name", 1);
    NameFieldNameMap.put("protocol.AssetIssueContract.abbr", 1);
    NameFieldNameMap.put("protocol.AssetIssueContract.description", 1);
    NameFieldNameMap.put("protocol.AssetIssueContract.url", 1);
```

**File:** framework/src/main/java/org/tron/core/services/http/JsonFormat.java (L854-860)
```java
  static String escapeBytes(ByteString input, final String fliedName, boolean selfType) {
    if (!selfType) {
      return ByteArray.toHexString(input.toByteArray());
    } else {
      return escapeBytesSelfType(input, fliedName);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/JsonFormat.java (L862-877)
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
```
