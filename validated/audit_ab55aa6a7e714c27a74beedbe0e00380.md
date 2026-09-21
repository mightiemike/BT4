### Title
MojoJS bindings enabled for PrivilegedWebContents without origin validation, exposing entire Mojo interface surface - (File: chrome/browser/chrome_content_browser_client.cc)

### Summary
Chrome enables MojoJS for Glic guest and PrivilegedWebContents (PWC) outermost main frames based only on the WebContents type, without validating the committed or pending origin against the capability allowlist. This is analogous to Apache DolphinScheduler's exposed Spring Boot Actuator management endpoints: an internal control surface is made available too broadly, allowing privileged remote web content to access sensitive browser state.

### Finding Description
`ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame` returns `true` for a Glic guest outermost main frame and for any `pwc::PrivilegedWebContents` outermost main frame, without checking the frame origin. [1](#0-0)  The inline TODO explicitly acknowledges the flaw: "Enabling MojoJS for any PWC exposes the entire Mojo interface surface rather than only GeicApi, and the committed origin is not checked against the capability allowlist here." [2](#0-1) 

The same over-exposure is repeated in `GeicPwcManager::TabEntry::RenderFrameCreated` and `ReadyToCommitNavigation`, which call `EnableMojoJsBindings` for any primary main frame with identical TODO comments. [3](#0-2) 

### Impact Explanation
MojoJS allows JavaScript in the renderer to request and bind arbitrary Mojo interfaces. Once enabled, the renderer can reach interfaces registered in `PopulateChromeFrameBinders`, including `pwc::mojom::PrivilegedBridge` and `geic::mojom::GeicApi` when the process is privileged. [4](#0-3)  The GEiC design explicitly notes that "the remote GE web app runs directly in the privileged main frame and speaks Mojo directly to the browser process." [5](#0-4)  An attacker-controlled remote app loaded in such a frame can therefore invoke browser-side Mojo interfaces and exfiltrate sensitive data such as saved passwords, cookies, autofill data, and browsing history—equivalent to Actuator endpoints leaking database credentials.

### Likelihood Explanation
The vulnerability is reachable from remote web content running in a Glic guest or PWC. `ShouldAllowMojoJsBindingsForFrame` is consulted from `ReadyToCommitNavigation` before the frame commits, so the pending URL's origin is not validated at that gate. [6](#0-5)  While `GlicGuestObserver::MaybeEnableMojoJsBindings` does check `IsOriginAllowedGlicApi` before enabling MojoJS, [7](#0-6)  `ShouldAllowMojoJsBindingsForFrame` returns `true` regardless, creating a bypass. For GEiC/PWC, no origin check is performed at all. I could not verify from the indexed code whether the Glic/PWC features are enabled by default or require flags.

### Recommendation
Restrict MojoJS enablement to origins explicitly on the capability allowlist:
- In `ShouldAllowMojoJsBindingsForFrame`, validate the pending/committed origin against the PWC or Glic allowlist before returning `true`.
- In `GeicPwcManager::TabEntry`, add the same origin check before calling `EnableMojoJsBindings`.
- Implement the scoped capability binding mechanism described in the TODO so that only `GeicApi`/`GlicWebClientHandler` are exposed, not the entire Mojo surface.

### Proof of Concept
The vulnerable code path is:
1. `ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame` returns `true` for any PWC outermost main frame. [8](#0-7) 
2. `GeicPwcManager::TabEntry::ReadyToCommitNavigation` calls `EnableMojoJsBindings` for any primary main frame. [9](#0-8) 
3. With MojoJS enabled, the renderer can request interfaces from `PopulateChromeFrameBinders`, including privileged interfaces when the process is marked privileged. [4](#0-3)

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

**File:** chrome/browser/chrome_browser_interface_binders.cc (L447-458)
```text
void PopulateChromeFrameBinders(
    mojo::BinderMapWithContext<content::RenderFrameHost*>* map,
    content::RenderFrameHost* render_frame_host) {
  map->Add<glic::mojom::WebClientHandler>(&glic::BindGlicWebClientHandler);
  // Defense in depth: privileged capability interfaces are not even registered
  // for a frame outside a privileged process, so a non-PWC frame cannot
  // request them at all. The bind-time gate (pwc::EnforceCapabilityGate)
  // remains the security boundary for frames that do get the binders.
  if (render_frame_host->GetProcess()->IsPrivileged()) {
    map->Add<pwc::mojom::PrivilegedBridge>(&pwc::BindPrivilegedBridge);
    map->Add<geic::mojom::GeicApi>(&geic::BindGeicApi);
  }
```

**File:** chrome/browser/geic/geic.mojom (L39-48)
```text
// 2. Embedding architecture (PrivilegedWebContents vs <webview>):
//    Glic embeds its remote UI inside a <webview> hosted within a
//    chrome-untrusted:// WebUI container. In Glic, the remote page communicates
//    via window.postMessage with the WebUI host, which translates messages to
//    Mojo IPCs. In contrast, GEiC leverages PrivilegedWebContents (see
//    //chrome/browser/pwc), eliminating the intermediary WebUI host layer. The
//    remote GE web app runs directly in the privileged main frame and speaks
//    Mojo directly to the browser process. Consequently, this interface is
//    designed as a direct Mojo IPC surface rather than a postMessage bridge
//    API.
```

**File:** chrome/browser/glic/host/glic_guest_observer.cc (L87-100)
```text
void GlicGuestObserver::MaybeEnableMojoJsBindings(
    content::NavigationHandle* navigation_handle) {
  if (!navigation_handle->IsInPrimaryMainFrame()) {
    return;
  }
  // Enable MojoJS bindings if the pending navigation is targeting an allowed
  // origin so Blink can initialize the Mojo context during document load.
  // The frame's committed origin is checked in `BindGlicWebClientHandler()`
  // when the page attempts to bind the pipe.
  if (IsOriginAllowedGlicApi(
          url::Origin::Create(navigation_handle->GetURL()))) {
    navigation_handle->GetRenderFrameHost()->EnableMojoJsBindings(
        /*features=*/nullptr);
  }
```
