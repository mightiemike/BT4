[1](#0-0)

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetAccountBalanceServlet.java (L19-29)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      BalanceContract.AccountBalanceRequest.Builder builder
          = BalanceContract.AccountBalanceRequest.newBuilder();
      JsonFormat.merge(params.getParams(), builder, params.isVisible());
      fillResponse(params.isVisible(), builder.build(), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```
