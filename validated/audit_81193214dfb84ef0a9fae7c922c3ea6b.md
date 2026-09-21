### Title
Missing origin check when enabling full MojoJS bindings on GEIC PrivilegedWebContents main-frame navigation - (File: chrome/browser/geic/geic_pwc_manager.cc)

### Summary
The reported bug class is a Connext `onlyConnext` modifier that checks *who* sent a cross-chain call (`msg.sender == connext`) but not *which domain/origin* it actually originated from, letting any origin's call through. The same class of bug — authenticate the channel/container but never validate the origin of the content running inside it before granting a privileged capability — exists in Chromium's GEIC (Gemini Enterprise in Chrome) `PrivilegedWebContents` (PWC) MojoJS-binding logic.

### Finding Description
`GeicPwcManager::TabEntry::ReadyToCommitNavigation()` unconditionally calls `EnableMojoJsBindings()` on the primary main frame of a GEIC `PrivilegedWebContents`, with no check of the committing origin against the PWC's capability allowlist: [1](#0-0) 

The same gap is documented and mirrored in `ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame()`, which grants MojoJS to *any* main frame of *any* `PrivilegedWebContents` solely because the frame is the outermost main frame of a PWC — again, without checking the frame's `GetLastCommittedOrigin()` against the PWC's `capability_allowlist_`: [2](#0-1) 

`GeicPwcManager::TabEntry::RenderFrameCreated()` similarly enables MojoJS bindings on new-frame creation for the primary main frame with the identical caveat noted inline: [3](#0-2) 

By contrast, the narrower `geic::mojom::GeicApi` interface *is* properly gated: `BindGeicApi()` calls `pwc::EnforceCapabilityGate(render_frame_host)`, which validates the frame against the PWC's capability policy and terminates the renderer via `bad_message::ReceivedBadMessage()` on violation: [4](#0-3) 

However, `EnableMojoJsBindings()` is a strictly more powerful primitive: it exposes the *entire* internal Mojo interface surface registered for that frame (raw `Mojo.bindInterface()` from JS), not just `GeicApi`. This gap is explicitly acknowledged by the authors in three separate `TODO(crbug.com/539909218)` comments across `chrome_content_browser_client.cc` and `geic_pwc_manager.cc`, all stating "the committed origin is not checked against the capability allowlist here."

The `PrivilegedWebContents` is normally navigated only to a fixed, allowlisted `dev_url_` [5](#0-4) , and a separate `PwcNavigationThrottle` is registered to cancel off-allowlist main-frame navigations [6](#0-5) . The `ReadyToCommitNavigation()`/`RenderFrameCreated()` MojoJS-enabling hooks fire independently of (and, per the code comments, without waiting for or re-checking) that allowlist decision, meaning defense-in-depth against the origin check relies entirely on the navigation throttle being airtight for every path that can commit a document in the PWC's main frame (redirects, renderer-initiated navigations, error pages, `about:blank`, etc.) — the same "only checked the messenger, not the origin" pattern as the reported Connext issue.

### Impact Explanation
If any main-frame navigation path in a GEIC `PrivilegedWebContents` can commit an attacker-controlled or cross-origin document before/without the `PwcNavigationThrottle` blocking it (e.g., a race, a redirect chain, or a commit-without-URL-loader edge case), that document gets `EnableMojoJsBindings()` — granting it the entire internal Mojo interface surface normally reserved for privileged browser-trusted code. This is a universal capability/sandbox-escape-class primitive: MojoJS exposes arbitrary registered Mojo interfaces to JavaScript, effectively removing the web/browser trust boundary for that document. This maps to a Critical/High severity class (equivalent to a privileged-origin bypass leading to full Mojo IPC access), consistent with the reported bug's "any call not intended by tokensoft will go through."

### Likelihood Explanation
The vulnerable code paths are guarded by a separate `PwcNavigationThrottle` and are gated behind the `pwc::mojom::features::kPrivilegedWebContents` feature and GEIC-specific enablement (`geic_enabling.h`), so this is not reachable from ordinary content today without that feature/component being active. The authors' own TODO comments frame this explicitly as a known, temporary "prototype shortcut" security gap pending a "scoped capability binding mechanism," which corroborates that the origin check is genuinely absent rather than performed elsewhere. Full exploitability depends on identifying a concrete navigation path that bypasses `PwcNavigationThrottle` and still triggers `ReadyToCommitNavigation`/`RenderFrameCreated` on the main frame — this was not verified in available source (the throttle's implementation, `pwc/pwc_navigation_throttle.cc`, was not indexed/available), so likelihood is Medium pending confirmation of such a bypass.

### Recommendation
In `GeicPwcManager::TabEntry::ReadyToCommitNavigation()`, `RenderFrameCreated()`, and `ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame()`, validate the frame's origin (`GetLastCommittedOrigin()` / the navigation's committing URL) against the `PrivilegedWebContents`'s `capability_allowlist_` before calling `EnableMojoJsBindings()`, mirroring the check already performed by `pwc::EnforceCapabilityGate()` for `GeicApi`. Do not rely solely on the navigation throttle as the sole enforcement point for a security boundary this powerful.

### Proof of Concept
Not independently reproducible from the indexed source alone: the concrete navigation bypass of `PwcNavigationThrottle` (source not available in this index) that would let an attacker-controlled origin reach `ReadyToCommitNavigation`/`RenderFrameCreated` as the GEIC PWC's primary main frame was not confirmed. The analog is grounded in the explicit, author-acknowledged code comments confirming the missing origin check at the three cited call sites.

### Citations

**File:** chrome/browser/geic/geic_pwc_manager.cc (L103-118)
```text
  if (dev_url_.is_empty() || !dev_url_.is_valid()) {
    DVLOG(1)
        << "GEiC guest URL is not configured; skipping PWC initialization.";
    return nullptr;
  }

  url::Origin origin = url::Origin::Create(dev_url_);
  auto policy_delegate = std::make_unique<pwc::FixedPwcPolicyDelegate>(
      /*navigation_allowlist=*/std::vector<url::Origin>{origin},
      /*capability_allowlist=*/std::vector<url::Origin>{origin});

  auto pwc = pwc::PrivilegedWebContents::Create(
      pwc::PrivilegedComponent::kGeic, profile_, std::move(policy_delegate));
  if (!pwc || !pwc->web_contents()) {
    return nullptr;
  }
```

**File:** chrome/browser/geic/geic_pwc_manager.cc (L192-200)
```text
void GeicPwcManager::TabEntry::RenderFrameCreated(
    content::RenderFrameHost* render_frame_host) {
  // TODO(crbug.com/539909218): Prototype shortcut. This exposes the entire
  // Mojo surface rather than only GeicApi, and the committed origin is not
  // checked against the capability allowlist here.
  if (render_frame_host->IsInPrimaryMainFrame()) {
    render_frame_host->EnableMojoJsBindings(/*features=*/nullptr);
  }
}
```

**File:** chrome/browser/geic/geic_pwc_manager.cc (L202-211)
```text
void GeicPwcManager::TabEntry::ReadyToCommitNavigation(
    content::NavigationHandle* navigation_handle) {
  // TODO(crbug.com/539909218): Prototype shortcut. This exposes the entire
  // Mojo surface rather than only GeicApi, and the committed origin is not
  // checked against the capability allowlist here.
  if (navigation_handle->IsInPrimaryMainFrame() &&
      navigation_handle->GetRenderFrameHost()) {
    navigation_handle->GetRenderFrameHost()->EnableMojoJsBindings(
        /*features=*/nullptr);
  }
```

**File:** chrome/browser/chrome_content_browser_client.cc (L2277-2289)
```text
  // TODO(crbug.com/539909218): Prototype shortcut. Enabling MojoJS for any PWC
  // exposes the entire Mojo interface surface rather than only GeicApi, and the
  // committed origin is not checked against the capability allowlist here.
  // Gating on the outermost main frame is a stopgap while erikchen@ designs a
  // scoped capability binding mechanism in follow-ups. We check
  // `!render_frame_host.GetParentOrOuterDocument()` rather than
  // `IsInPrimaryMainFrame()` because this predicate is consulted from
  // `ReadyToCommitNavigation` before the frame commits, where
  // lifecycle-dependent queries return false.
  if (!render_frame_host.GetParentOrOuterDocument() && web_contents &&
      pwc::PrivilegedWebContents::FromWebContents(web_contents)) {
    return true;
  }
```

**File:** chrome/browser/geic/geic_host.cc (L39-57)
```text
void BindGeicApi(content::RenderFrameHost* render_frame_host,
                 mojo::PendingReceiver<mojom::GeicApi> receiver) {
  // The shared gate enforces the full capability policy (and terminates the
  // renderer itself on a renderer-controllable violation); null simply means
  // do not bind.
  pwc::PrivilegedWebContents* privileged =
      pwc::EnforceCapabilityGate(render_frame_host);
  if (!privileged) {
    return;
  }

  // The gate is component-agnostic; GeicApi additionally requires the
  // component to be GEIC. A request from another component's frame is again a
  // compromised renderer.
  if (privileged->component() != pwc::PrivilegedComponent::kGeic) {
    bad_message::ReceivedBadMessage(render_frame_host->GetProcess(),
                                    bad_message::PWC_BRIDGE_UNQUALIFIED_FRAME);
    return;
  }
```

**File:** chrome/browser/chrome_content_browser_client_navigation_throttles.cc (L634-646)
```text
  glic::GlicGuestNavigationThrottle::MaybeCreateAndAdd(registry);

  pwc::PwcNavigationThrottle::MaybeCreateAndAdd(registry);
}

void CreateAndAddChromeThrottlesForCommitWithoutUrlLoader(
    content::NavigationThrottleRegistry& registry) {
  // PwcNavigationThrottle must also cancel off-allowlist main-frame
  // navigations that commit without a URL loader (e.g. a subframe navigating
  // the main frame to about:blank), which never reach WillStartRequest().
  pwc::PwcNavigationThrottle::MaybeCreateAndAdd(registry);
}

```
