### Title
Sensitive `Authorization`/credential headers set via WebView `loadUrl(url, extraHeaders)` survive same-host cross-scheme/cross-port redirects due to host-only origin comparison - (File: `android_webview/browser/network_service/aw_url_loader_throttle.cc`)

### Summary
`AwURLLoaderThrottle` decides whether to strip developer-supplied "extra headers" (which commonly include `Authorization` or session tokens) on a redirect by comparing only the domain/host of the original and redirect-target URLs, ignoring scheme and port. This mirrors the `@hapi/wreck` bug class (GHSA-x426-x7cc-3fpc): a hostname-only same-origin check that fails to catch scheme downgrades (HTTPS→HTTP) or port changes, allowing credential headers to leak to a different, potentially attacker-controlled service that shares only the hostname.

### Finding Description
`AwURLLoaderThrottle::WillStartRequest` attaches extra headers (from `AwBrowserContext::GetExtraHeadersForUrl`, populated by the WebView `loadUrl(url, extraHeaders)` embedder API) to the outgoing request and records the request's origin: [1](#0-0) 

On redirect, `WillRedirectRequest` only strips those headers if the new URL is judged to be a different domain, using `net::registry_controlled_domains::SameDomainOrHost`, which compares registrable domain/host only — not scheme or port: [2](#0-1) 

Because `SameDomainOrHost` treats `https://example.com` and `http://example.com:8080` as "the same domain," a redirect from the original HTTPS origin to a different scheme or port on the same host will NOT trigger header removal, so any sensitive header the app attached (e.g., `Authorization: Bearer <token>`) is forwarded verbatim to the new scheme/port. This is exactly the bug class in the report: hostname-only origin comparison used to decide whether to drop credential headers on redirect, missing scheme/port changes.

Notably, this contrasts with the newer, unrelated `AwOriginMatchedHeader` mechanism in the same codebase, which correctly matches using full `url::Origin`/`OriginMatcher` semantics (scheme, host, and port) via `AwOriginMatchedHeader::MatchesOrigin`: [3](#0-2) 
This shows the codebase itself distinguishes "same domain" (host-only) from "same origin" (scheme+host+port), and the legacy extra-headers redirect-stripping path uses the weaker check.

### Impact Explanation
If a WebView-hosting app calls `loadUrl(url, extraHeaders)` with a sensitive header (commonly `Authorization`), and the server at that host serves — or is coerced by a network-adjacent attacker/co-tenant into serving — a redirect to the same hostname but a different port or a downgraded scheme (HTTP), the credential header is silently forwarded to that new destination. An attacker controlling (or with access to) a service on an adjacent port of the same host, or capable of forcing an HTTPS→HTTP redirect, can capture bearer tokens/session credentials and impersonate the user against the upstream service. This is a cross-origin credential disclosure (CWE-200/CWE-522/CWE-346), matching the severity class of the original CVE.

### Likelihood Explanation
Exploitation requires: (1) the embedding app to have used the `loadUrl` extra-headers API with a sensitive header value, and (2) the target server (or an entity able to influence traffic to/around it, e.g., a same-host multi-tenant port or a network attacker able to inject/redirect via cleartext HTTP) to issue a same-host redirect that changes scheme or port. This is not reachable purely from arbitrary web content without the app opting into the extra-headers API, so it is somewhat constrained to specific WebView integration patterns rather than being universally exploitable from any page.

### Recommendation
Replace the `net::registry_controlled_domains::SameDomainOrHost` check in `AwURLLoaderThrottle::WillRedirectRequest` with a full-origin comparison (scheme + host + port) between `original_origin_` and `url::Origin::Create(redirect_info->new_url)`, so that any redirect crossing scheme or port boundaries strips the previously attached sensitive headers, consistent with the origin-based matching already used by `AwOriginMatchedHeader`.

### Proof of Concept
1. Embedding app calls `webView.loadUrl("https://victim.example.com/", {"Authorization": "Bearer <secret>"})`.
2. `AwURLLoaderThrottle::WillStartRequest` attaches the header and records `original_origin_ = https://victim.example.com`.
3. The server at `victim.example.com` (or an attacker with access to another port/service on that host, or a man-in-the-middle capable of forcing plaintext redirects) responds with `302 Location: http://victim.example.com:8080/collect` (same host, different scheme/port).
4. `WillRedirectRequest` calls `SameDomainOrHost(new_url, original_origin_)`, which returns `true` because the host/domain matches — despite the scheme/port differing.
5. The `Authorization` header is therefore not added to `removed_headers`, so it is replayed on the follow-up request to `http://victim.example.com:8080/collect`, leaking the bearer token to that endpoint in cleartext (or to an unintended co-hosted service).

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

**File:** android_webview/browser/http_headers/aw_origin_matched_header.cc (L34-36)
```text
bool AwOriginMatchedHeader::MatchesOrigin(const url::Origin& origin) const {
  return matcher_.Matches(origin);
}
```
