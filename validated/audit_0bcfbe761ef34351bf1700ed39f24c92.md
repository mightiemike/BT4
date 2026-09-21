## Analysis: Authorization/extra-header leak on same-domain, cross-scheme/port redirects in Android WebView

I found a direct structural analog to the Scrapy `Authorization`-header leak inside `android_webview/browser/network_service/aw_url_loader_throttle.cc`. It reuses exactly the same flawed check pattern as the reported Scrapy bug: it treats "same registrable domain" as sufficient to keep a sensitive header across a redirect, rather than requiring same-origin (scheme + host + port).

### Title
Same-domain but cross-scheme/cross-port redirects leak app-provided sensitive headers (including `Authorization`) in AwURLLoaderThrottle - (File: android_webview/browser/network_service/aw_url_loader_throttle.cc)

### Summary
`AwURLLoaderThrottle::WillRedirectRequest` decides whether to strip headers that were injected into the original request by checking `net::registry_controlled_domains::SameDomainOrHost()` between the redirect target and the original origin, and only removes the added headers when the domains differ. [1](#0-0) 

### Finding Description
`WillStartRequest` calls `AddExtraHeadersIfNeeded`, which pulls headers configured by the embedding app for a URL (via `AwBrowserContext::GetExtraHeadersForUrl`) and injects them into the outgoing request, tracking their names in `added_headers_` and recording `original_origin_`. [2](#0-1) [3](#0-2) 

When the server responds with a redirect, `WillRedirectRequest` is the only gate that decides whether these injected headers survive onto the new request. It computes `is_same_domain` using `SameDomainOrHost()`, which compares only the registrable domain/host component of the URLs — it ignores scheme and port entirely (this mirrors exactly the semantics of the fixed-in-2.11.1 Scrapy check that only compared hostnames). If `is_same_domain` is true, the headers (which could include things like `Authorization`, cookies, or other app-configured secrets) are **not** added to `removed_headers`, so they are carried forward unmodified to the new destination: [4](#0-3) 

Because a redirect from `https://example.com` to `http://example.com` (scheme downgrade) or from `https://example.com:443` to `https://example.com:8443` (port change controlled by the destination) is still considered "same domain" by `SameDomainOrHost`, the added headers leak across what is actually a different origin per the same-origin policy definition (scheme+host+port). This is the exact bug class from the Scrapy advisory: dropping the header on cross-domain redirects but incorrectly retaining it when only scheme/port change.

### Impact Explanation
If the embedding WebView app has configured an `Authorization` header (or any other sensitive header) for a URL via the WebView "extra headers" API, and the server for that domain issues a same-domain but cross-scheme redirect (e.g., HTTP downgrade, which a network attacker performing a redirect/response-injection on the initial unencrypted leg — or simply a compromised/malicious first-party page under that domain — can trigger), the sensitive header value is disclosed to the http endpoint. This is a `CWE-200` information disclosure of a credential, matching the original advisory's severity class (Medium, `C:H`).

### Likelihood Explanation
This requires: (1) the host app to have registered an extra/auth header for a URL (a supported, non-flag-gated WebView API), and (2) the server at that domain to issue a redirect to a different scheme/port on the same registrable domain — something outside Chromium's control and fully reachable via a normal network response, no extensions/enterprise policy/local access required. This is analogous to the "man-in-the-middle can access Authorization header" scenario described in the original advisory.

### Recommendation
Change the redirect-time header-retention check in `AwURLLoaderThrottle::WillRedirectRequest` from a domain-only comparison (`SameDomainOrHost`) to a strict same-origin comparison (scheme, host, and port must all match) using `url::Origin::IsSameOriginWith()` against `original_origin_`, so headers are stripped whenever scheme or port changes, not just when the domain changes.

### Proof of Concept
1. Host app calls the WebView API to set an `Authorization` (or other custom) header for `https://victim.example`.
2. WebView loads `https://victim.example`, and `AddExtraHeadersIfNeeded` attaches the header; `original_origin_` is recorded as `https://victim.example`.
3. The server (or an attacker able to influence a redirect response on that domain) responds with a redirect to `http://victim.example/...` (same registrable domain, different scheme) or to a different port.
4. `WillRedirectRequest` computes `is_same_domain = true` via `SameDomainOrHost`, so the header is not placed into `removed_headers` and is retained.
5. The follow-up request to `http://victim.example` (plaintext) or the alternate port carries the sensitive header, exposing it to network observers/MITM on that leg — the same disclosure scenario described in GHSA-4qqq-9vqf-3h3f.

### Citations

**File:** android_webview/browser/network_service/aw_url_loader_throttle.cc (L21-27)
```text
void AwURLLoaderThrottle::WillStartRequest(network::ResourceRequest* request,
                                           bool* defer) {
  AddExtraHeadersIfNeeded(request->url, &request->headers);
  if (!added_headers_.empty()) {
    original_origin_ = url::Origin::Create(request->url);
  }
}
```

**File:** android_webview/browser/network_service/aw_url_loader_throttle.cc (L29-48)
```text
void AwURLLoaderThrottle::WillRedirectRequest(
    net::RedirectInfo* redirect_info,
    const network::mojom::URLResponseHead& response_head,
    bool* defer,
    network::HttpRequestHeadersUpdateParams* headers_update_params) {
  if (!added_headers_.empty()) {
    bool is_same_domain = net::registry_controlled_domains::SameDomainOrHost(
        redirect_info->new_url, original_origin_,
        net::registry_controlled_domains::INCLUDE_PRIVATE_REGISTRIES);

    if (!is_same_domain) {
      // The headers we added must be removed.
      headers_update_params->removed_headers.insert(
          headers_update_params->removed_headers.end(),
          std::make_move_iterator(added_headers_.begin()),
          std::make_move_iterator(added_headers_.end()));
      added_headers_.clear();
    }
  }
}
```

**File:** android_webview/browser/network_service/aw_url_loader_throttle.cc (L50-66)
```text
void AwURLLoaderThrottle::AddExtraHeadersIfNeeded(
    const GURL& url,
    net::HttpRequestHeaders* headers) {
  std::string extra_headers = aw_browser_context_->GetExtraHeadersForUrl(url);
  if (extra_headers.empty())
    return;

  net::HttpRequestHeaders temp_headers;
  temp_headers.AddHeadersFromString(extra_headers);
  for (net::HttpRequestHeaders::Iterator it(temp_headers); it.GetNext();) {
    if (headers->HasHeader(it.name()))
      continue;

    headers->SetHeader(it.name(), it.value());
    added_headers_.push_back(it.name());
  }
}
```
