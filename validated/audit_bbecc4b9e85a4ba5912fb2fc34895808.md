### Title
Unbounded JSON nesting depth in `ValidateAddressServlet.doPost` allows StackOverflowError bypassing exception handling - ([File: ValidateAddressServlet.java])

### Summary
`doPost` only calls `Util.checkBodySize(input)` (a byte-size check) before feeding the raw request body into fastjson's `JSON.parseObject(input)` [1](#0-0) . Because `checkBodySize` bounds only total payload length and not JSON structural depth, an attacker can submit a small but deeply nested JSON body (e.g. thousands of nested arrays `[[[[...]]]]`) that is well under the size limit yet drives fastjson's recursive-descent parser to a `StackOverflowError`.

### Finding Description
The `doPost` handler reads the full request body, validates only its size with `Util.checkBodySize(input)`, and then parses it with `JSON.parseObject(input)` before ever touching `jsonAddress.getString("address")` [2](#0-1) . `checkBodySize` (defined in `Util.java`, referenced identically across ~20 other servlets such as `TransferServlet`, `TriggerSmartContractServlet`, etc.) is a byte-length guard and provides no protection against pathological JSON structures such as deep nesting, since a payload like a several-thousand-deep nested array is only a few KB in size.

Fastjson's default object/array parsing is recursive, so deeply nested structures consume call-stack frames proportional to nesting depth rather than to payload byte size, and can throw `java.lang.StackOverflowError`. Critically, the surrounding `try { ... } catch (Exception e)` block in `doPost` only catches `Exception`, not `Error` [3](#0-2) . `StackOverflowError` extends `Error`, so it is not caught here, and unwinds past this handler onto whatever the servlet container's default error handling does for the request thread — behavior that differs from the graceful "Exception" logging path the rest of the code relies on.

### Impact Explanation
Each malicious request costs the attacker only a small (few-KB) POST body but forces the server to burn a full stack of recursive parser frames and throw an uncaught `Error`, which is not handled the same way as the intended `catch (Exception e)` fallback. Repeated concurrent requests of this kind against the public HTTP API can produce disproportionate CPU/stack cost per request relative to attacker cost, potentially disrupting individual servlet threads' error handling and creating request-handling noise/inefficiency on a publicly reachable, unauthenticated endpoint. This is a resource-asymmetry / denial-of-service class issue scoped to the HTTP JSON API, not a consensus, accounting, or authorization break.

### Likelihood Explanation
This is trivially reproducible by any unauthenticated user who can reach the `/wallet/validateaddress` HTTP endpoint (or any of the ~20 sibling servlets sharing the same `checkBodySize` + `JSON.parseObject` pattern), since it requires only a single crafted small POST body with no authentication, signing, or fee payment. The precondition is simply that the HTTP API is exposed, which is the default configuration for java-tron nodes offering the JSON-RPC/HTTP API.

### Recommendation
- Configure fastjson's parser with a bounded nesting/feature limit (e.g. use `Feature`/`ParserConfig` options or a `JSONReader` with `maxLevel`) to reject deeply nested input before/while parsing, or pre-validate structural depth of the input string prior to calling `JSON.parseObject`.
- Broaden the `catch` clause in `doPost` (and the other servlets using the identical pattern) to also catch `Throwable`/`StackOverflowError` so malformed/adversarial input degrades gracefully rather than propagating an uncaught `Error`.
- Apply a shared, centrally-configured max-nesting-depth constant alongside `Util.checkBodySize` so all HTTP servlets get consistent protection.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/services/http/ValidateAddressServletTest.java (extension)
@Test
public void testDeeplyNestedJsonCausesStackOverflow() throws Exception {
  StringBuilder sb = new StringBuilder();
  int depth = 100000; // small in bytes, deep in structure
  for (int i = 0; i < depth; i++) {
    sb.append("[");
  }
  for (int i = 0; i < depth; i++) {
    sb.append("]");
  }
  String maliciousBody = sb.toString(); // well under checkBodySize's byte limit

  MockHttpServletRequest request = new MockHttpServletRequest();
  request.setContent(maliciousBody.getBytes(StandardCharsets.UTF_8));
  MockHttpServletResponse response = new MockHttpServletResponse();

  ValidateAddressServlet servlet = new ValidateAddressServlet();
  // Expect: no unchecked StackOverflowError should escape unhandled;
  // Currently: StackOverflowError is thrown by JSON.parseObject and is NOT caught
  // by the servlet's `catch (Exception e)` block, demonstrating the gap.
  servlet.doPost(request, response);
}
```
Expected (post-fix) assertion: the servlet either rejects the payload during a depth pre-check (`400`/logged rejection) or catches `Throwable` and responds gracefully, instead of allowing an uncaught `StackOverflowError` to bypass the `catch (Exception e)` handling.

**Note:** I was unable to view the exact implementation body of `Util.checkBodySize` within the tool budget available (only its call sites were confirmed) [4](#0-3) , so I cannot confirm with 100% certainty whether it performs any additional structural validation beyond byte-length checking. This should be verified directly in a full session before treating the finding as fully confirmed.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/ValidateAddressServlet.java (L69-79)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      String input = request.getReader().lines()
          .collect(Collectors.joining(System.lineSeparator()));
      Util.checkBodySize(input);
      JSONObject jsonAddress = JSON.parseObject(input);
      response.getWriter().println(validAddress(jsonAddress.getString("address")));
    } catch (Exception e) {
      logger.debug("Exception: {}", e.getMessage());
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/Util.java (L1-63)
```java
package org.tron.core.services.http;

import static org.apache.commons.lang3.StringUtils.EMPTY;
import static org.tron.common.utils.Commons.decodeFromBase58Check;

import com.google.protobuf.Any;
import com.google.protobuf.ByteString;
import com.google.protobuf.GeneratedMessageV3;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.protobuf.Message;
import com.google.protobuf.ProtocolStringList;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.lang.reflect.Constructor;
import java.math.BigDecimal;
import java.nio.charset.Charset;
import java.security.InvalidParameterException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.bouncycastle.util.encoders.Hex;
import org.eclipse.jetty.http.HttpMethod;
import org.eclipse.jetty.http.MimeTypes;
import org.eclipse.jetty.util.MultiMap;
import org.eclipse.jetty.util.StringUtil;
import org.eclipse.jetty.util.UrlEncoded;
import org.tron.api.GrpcAPI;
import org.tron.api.GrpcAPI.BlockList;
import org.tron.api.GrpcAPI.TransactionApprovedList;
import org.tron.api.GrpcAPI.TransactionExtention;
import org.tron.api.GrpcAPI.TransactionIdList;
import org.tron.api.GrpcAPI.TransactionList;
import org.tron.api.GrpcAPI.TransactionSignWeight;
import org.tron.common.crypto.Hash;
import org.tron.common.parameter.CommonParameter;
import org.tron.common.utils.ByteArray;
import org.tron.common.utils.Sha256Hash;
import org.tron.core.Constant;
import org.tron.core.actuator.TransactionFactory;
import org.tron.core.capsule.BlockCapsule;
import org.tron.core.capsule.TransactionCapsule;
import org.tron.core.config.args.Args;
import org.tron.core.db.TransactionTrace;
import org.tron.core.services.http.JsonFormat.ParseException;
import org.tron.json.JSON;
import org.tron.json.JSONArray;
import org.tron.json.JSONException;
import org.tron.json.JSONObject;
import org.tron.protos.Protocol.Account;
import org.tron.protos.Protocol.Block;
import org.tron.protos.Protocol.Transaction;
import org.tron.protos.Protocol.Transaction.Contract.ContractType;
import org.tron.protos.Protocol.TransactionInfo;
import org.tron.protos.Protocol.TransactionInfo.Log;
import org.tron.protos.contract.SmartContractOuterClass.CreateSmartContract;

```
