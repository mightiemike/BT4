### Title
MojoJS enabled for PrivilegedWebContents main frames without capability-origin allowlist check - (File: chrome/browser/chrome_content_browser_client.cc)

### Summary
Browser-side code enables the full Mojo JS binding surface for any outermost main frame inside a PrivilegedWebContents (PWC), but the privileged Mojo interfaces that surface exposes are gated at bind time on a per-origin capability allowlist. The outer enablement predicate is therefore strictly weaker than the inner binding predicate, matching the Solidity pattern where an `onlyTellerV2` caller invokes an `onlyOwner` callee and the call fails when the two principals differ.

### Finding Description
`ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame` returns `true` for any outermost main frame of a PWC or Glic guest without verifying that the frame's committed origin is on the component's capability allowlist [1](#0-0) . The GEiC PWC observer repeats the same shortcut: `GeicPwcManager::TabEntry::RenderFrameCreated` and `ReadyToCommitNavigation` call `EnableMojoJsBindings` for every primary main frame, with an explicit TODO noting that "the committed origin is not checked against the capability allowlist here" [2](#0-1) .

By contrast, the actual privileged interface binders enforce a much stricter gate. `pwc::EnforceCapabilityGate` requires the frame to be in a PWC, to be the outermost main frame, to have committed an HTTPS URL, and to have a committed origin that the component's policy lists as a capability origin; it also requires the process to be origin-keyed [3](#0-2) . `geic::BindGeicApi` additionally checks that the PWC serves the `kGeic` component [4](#0-3) .

This is the same class of mismatch as the Teller report: the outer authorization (PWC outermost main frame / `onlyTellerV2`) does not imply the inner authorization (capability allowlist origin / `onlyOwner`), so the inner gate can reject a caller that the outer gate already let through.

### Impact Explanation
If a PWC main frame commits to an origin that is not on the capability allowlist, MojoJS is still injected into the renderer even though `EnforceCapabilityGate` will later refuse to bind `GeicApi` or `PrivilegedBridge`. The renderer therefore holds the full Mojo JS surface against origins that were never meant to hold capabilities. A compromised renderer can use that surface to probe or attack browser-process Mojo handlers, increasing the reachable attack surface for sandbox escape or cross-origin capability misuse. The navigation throttle and binding gate are fail-closed, but the unnecessary MojoJS exposure is the security-relevant gap.

### Likelihood Explanation
`PwcNavigationThrottle::CheckUrl` normally cancels primary main-frame navigations to origins outside the navigation allowlist [5](#0-4) , and `EnforceCapabilityGate` rejects inherited-scheme documents such as `about:blank`, `data:`, `blob:`, and `filesystem:` by requiring the committed URL itself to be HTTPS [6](#0-5) . However, MojoJS enablement happens earlier and with a weaker predicate, so any bypass, redirect, or race that lands a non-allowlisted document in the PWC main frame leaves MojoJS enabled. The code contains acknowledged prototype shortcuts (crbug.com/539909218) documenting the missing origin check.

### Recommendation
Do not enable MojoJS until the frame has passed the same capability gate used at bind time. Move the `EnableMojoJsBindings` call behind `pwc::IsCapabilityQualifiedFrame` (or a successful `EnforceCapabilityGate` check) and gate on the committed origin rather than the pending URL. For GEiC, scope MojoJS to the specific `GeicApi` interface instead of exposing the entire Mojo surface, as the TODO already requests.

### Proof of Concept
The existing `GeicApiBrowserTest::NonGeicComponentKilled` test demonstrates the bind-time gate terminating a renderer that requests `GeicApi` from a PWC serving a different component [7](#0-6) , confirming that the inner gate is the real security boundary. The TODO comments in `chrome/browser/chrome_content_browser_client.cc` and `chrome/browser/geic/geic_pwc_manager.cc` confirm that MojoJS is enabled before that boundary is evaluated.

### Citations

**File:** chrome/browser/chrome_content_browser_client.cc (L2269-2311)
```text
bool ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame(
    content::RenderFrameHost& render_frame_host) {
  content::WebContents* web_contents =
      content::WebContents::FromRenderFrameHost(&render_frame_host);
  if (web_contents && glic::IsGlicGuest(web_contents) &&
      !render_frame_host.GetParentOrOuterDocument()) {
    return true;
  }
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
#if BUILDFLAG(ENABLE_EXTENSIONS_CORE)
  const GURL& site_url = render_frame_host.GetSiteInstance()
                             ->GetSecurityPrincipal()
                             .GetDeprecatedSiteURL();
  if (site_url.SchemeIs(extensions::kExtensionScheme)) {
    content::BrowserContext* browser_context =
        render_frame_host.GetBrowserContext();
    const extensions::Extension* extension =
        extensions::ExtensionRegistry::Get(browser_context)
            ->enabled_extensions()
            .GetByID(site_url.GetHost());
    if (!extension) {
      return false;
    }
    const auto* registry =
        extensions::ExtensionMojoBinderRegistryFactory::GetForBrowserContext(
            browser_context);
    return registry && registry->IsMojoJsEnabledForFrame(*extension);
  }
#endif
  return false;
}
```

**File:** chrome/browser/geic/geic_pwc_manager.cc (L192-211)
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

**File:** chrome/browser/pwc/pwc_api_binder.cc (L43-104)
```text
bool IsStructurallyQualified(content::RenderFrameHost* render_frame_host,
                             PrivilegedWebContents* privileged) {
  return privileged && !render_frame_host->GetParentOrOuterDocument() &&
         render_frame_host->GetLastCommittedURL().SchemeIs(url::kHttpsScheme) &&
         privileged->policy().IsCapabilityOrigin(
             render_frame_host->GetLastCommittedOrigin());
}

// Tier 2 of the capability gate -- conditions a well-behaved renderer cannot
// control or observe race-free:
// - The document must currently be the primary main frame. A legitimate
//   request can be in flight while the browser commits a cross-document
//   navigation that moves the requesting document out of the primary page
//   (pending deletion). (A privileged WebContents never hosts prerendered or
//   back-forward-cached pages, so pending deletion is the only such state.)
//   Checking IsInPrimaryMainFrame() rather than IsOutermostMainFrame() also
//   keeps the gate correct if the PWC is ever embedded.
// - The document must actually run in an origin-keyed process: in a merely
//   site-keyed privileged process, a same-site cross-origin subframe could
//   share the qualifying frame's process, so such a process must not hold
//   capabilities.
bool IsLifecycleAndIsolationQualified(
    content::RenderFrameHost* render_frame_host) {
  return render_frame_host->IsInPrimaryMainFrame() &&
         render_frame_host->GetSiteInstance()
             ->GetSecurityPrincipal()
             .IsOriginKeyed();
}

}  // namespace

bool IsCapabilityQualifiedFrame(content::RenderFrameHost* render_frame_host) {
  content::WebContents* web_contents =
      content::WebContents::FromRenderFrameHost(render_frame_host);
  PrivilegedWebContents* privileged =
      PrivilegedWebContents::FromWebContents(web_contents);
  return IsStructurallyQualified(render_frame_host, privileged) &&
         IsLifecycleAndIsolationQualified(render_frame_host);
}

PrivilegedWebContents* EnforceCapabilityGate(
    content::RenderFrameHost* render_frame_host) {
  content::WebContents* web_contents =
      content::WebContents::FromRenderFrameHost(render_frame_host);
  PrivilegedWebContents* privileged =
      PrivilegedWebContents::FromWebContents(web_contents);

  // Fail closed, in two tiers. A tier-1 (structural) violation is a
  // compromised renderer and terminates the process; a tier-2 failure is not
  // renderer-controllable, so the receiver is dropped without a kill.
  if (!IsStructurallyQualified(render_frame_host, privileged)) {
    bad_message::ReceivedBadMessage(render_frame_host->GetProcess(),
                                    bad_message::PWC_BRIDGE_UNQUALIFIED_FRAME);
    return nullptr;
  }

  if (!IsLifecycleAndIsolationQualified(render_frame_host)) {
    return nullptr;
  }

  return privileged;
}
```

**File:** chrome/browser/geic/geic_host.cc (L39-67)
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

  if (!GeicHost::Get(privileged->unowned_user_data_host())) {
    // The frame qualifies, but the GEIC component has not attached its GeicHost
    // to this PrivilegedWebContents. That is a browser-side setup gap, not a
    // compromised renderer, so drop the request rather than terminating the
    // renderer.
    return;
  }
  GeicApiBinding::Create(render_frame_host, std::move(receiver));
}
```

**File:** chrome/browser/pwc/pwc_navigation_throttle.cc (L69-84)
```text
content::NavigationThrottle::ThrottleCheckResult
PwcNavigationThrottle::CheckUrl() {
  content::NavigationHandle& handle = *navigation_handle();
  PrivilegedWebContents* privileged =
      PrivilegedWebContents::FromWebContents(handle.GetWebContents());
  // The throttle is only added for the primary main frame of a
  // PrivilegedWebContents (see MaybeCreateAndAdd).
  CHECK(privileged);
  // IsNavigationAllowed structurally enforces HTTPS in addition to the
  // component's navigation allowlist.
  if (!privileged->policy().IsNavigationAllowed(
          url::Origin::Create(handle.GetURL()))) {
    return CANCEL_AND_IGNORE;
  }
  return PROCEED;
}
```

**File:** chrome/browser/geic/geic_host_browsertest.cc (L151-162)
```text
// A PrivilegedWebContents that serves a different component (here the test
// component) must not receive GeicApi, even from its qualifying main frame: the
// interface is keyed on the component, not merely on being privileged.
IN_PROC_BROWSER_TEST_F(GeicApiBrowserTest, NonGeicComponentKilled) {
  const GURL capability = https_server_.GetURL("a.test", "/title1.html");
  std::unique_ptr<pwc::PrivilegedWebContents> privileged =
      MakePrivileged(pwc::PrivilegedComponent::kTestComponent, capability);
  content::WebContents* web_contents = privileged->web_contents();
  ASSERT_TRUE(content::NavigateToURL(web_contents, capability));

  ExpectBindKillsRenderer(web_contents->GetPrimaryMainFrame());
}
```
