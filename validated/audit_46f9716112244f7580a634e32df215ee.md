### Title
Geolocation permission delegation to installed webapp (TWA) bypasses main-origin check, unlike Notifications - (File: chrome/browser/geolocation/geolocation_permission_context_delegate_android.cc)

### Summary
CVE-2023-0133 describes a bypass of "main origin permission delegation" for permission prompts on Android via a crafted HTML page. In this codebase, the permission-delegation-to-installed-webapp (TWA/WebAPK) feature exists for both Notifications and Geolocation. The Notifications path explicitly guards against cross-origin (iframe) delegation, but the Geolocation path does not contain the equivalent guard.

### Finding Description
`NotificationPermissionContext::DecidePermission` explicitly rejects requests where the requesting frame's origin differs from the embedding (top-level) origin *before* it ever consults the Android installed-webapp bridge: [1](#0-0) 

Only after that same-origin check passes does it proceed to query `InstalledWebappBridge::DecidePermission` for TWA/WebAPK delegation: [2](#0-1) 

In contrast, `GeolocationPermissionContextDelegateAndroid::DecidePermission` performs no comparison between `request_data.requesting_origin` and the frame's embedding/top-level origin. It only checks whether the `WebContents` delegate reports an installed-webapp geolocation context, then unconditionally forwards the request — including its `requesting_origin`, taken directly from whatever `RenderFrameHost` issued it — to the same `InstalledWebappBridge::DecidePermission` entry point used for main-frame delegation: [3](#0-2) 

`InstalledWebappBridge::DecidePermission`/`decidePermission` (Java side) then resolves the permission purely from the passed `originUrl`/`lastCommittedUrl` and hands the decision to `PermissionUpdater.getLocationPermission`, with no notion of whether the calling frame was the top-level document or a subframe: [4](#0-3) 

Because Geolocation can be requested from a subframe when the parent grants it via Permissions Policy (`allow="geolocation"`), a cross-origin iframe embedded inside a page running as a Trusted Web Activity could trigger this same delegation path and have its permission decided by the TWA client app's Android location permission (`ACCESS_FINE_LOCATION`/`ACCESS_COARSE_LOCATION`) — a decision that is supposed to reflect trust granted to the *main* verified origin, not to an arbitrary embedded origin. This mirrors the root cause pattern described in CVE-2023-0133/crbug.com/1375132: permission delegation intended for the main origin is applied without verifying the requesting frame is that main origin.

### Impact Explanation
If reachable, a malicious or compromised subframe running inside a legitimate TWA could obtain the "ALLOW" geolocation permission that was only meant to be delegated to the top-level verified origin, without the user seeing any Chrome-level permission prompt for that subframe's origin. This is a cross-origin permission/security-boundary bypass consistent in class with the CVE (Medium severity, disclosure of sensitive capability without user consent for the actual requesting origin).

### Likelihood Explanation
Exploitability is gated on the page running as an installed Trusted Web Activity (a legitimate site the user has installed as a TWA) and on a cross-origin iframe with Permissions-Policy geolocation delegation being embedded on that page — conditions within a single page's control by the site/attacker content, not requiring flags, MITM, or physical access. I was not able to fully verify at this iteration whether an additional main-frame check exists further up the call chain (e.g., in `TabWebContentsDelegateAndroid::GetInstalledWebappGeolocationContext` or in `PermissionRequestManager`/`ContentSettingPermissionContextBase` before `DecidePermission` is invoked) that might restrict this path to main-frame-only requests before reaching `GeolocationPermissionContextDelegateAndroid::DecidePermission`. That verification was in progress when tool access ended, so this should be treated as a plausible but not fully confirmed analog — the asymmetry between the Notification path's explicit origin-equality check and the Geolocation path's absence of one is the strongest concrete evidence found.

### Recommendation
Add an explicit `request_data.requesting_origin == request_data.embedding_origin` (or top-level-frame) check in `GeolocationPermissionContextDelegateAndroid::DecidePermission` before delegating to `InstalledWebappBridge::DecidePermission`, mirroring the guard already present in `NotificationPermissionContext::DecidePermission`.

### Proof of Concept
Conceptual (not fully verified against upstream call-chain gating):
1. Attacker or site operator installs a page as a TWA where the top-level origin (`https://good-twa-site.example`) is verified and has an associated client app with Android `ACCESS_FINE_LOCATION` granted.
2. The top-level page embeds a cross-origin iframe (`https://attacker.example`) with `allow="geolocation"` permissions policy.
3. The iframe calls `navigator.geolocation.getCurrentPosition(...)`.
4. If the request reaches `GeolocationPermissionContextDelegateAndroid::DecidePermission` without a main-frame/origin-equality check, it is forwarded to `InstalledWebappBridge::DecidePermission` using the iframe's `requesting_origin`, and the TWA client app's Android permission state is used to decide the geolocation permission for `attacker.example`, without a Chrome permission prompt tied to that origin.

### Citations

**File:** chrome/browser/notifications/notification_permission_context.cc (L162-172)
```text
  // Permission requests for either Web Notifications and Push Notifications may
  // only happen on top-level frames and same-origin iframes. Usage will
  // continue to be allowed in all iframes: such frames could trivially work
  // around the restriction by posting a message to their Service Worker, where
  // showing a notification is allowed.
  if (request_data->requesting_origin != request_data->embedding_origin) {
    std::move(callback).Run(content::PermissionResult(
        blink::mojom::PermissionStatus::DENIED,
        content::PermissionStatusSource::UNSPECIFIED));
    return;
  }
```

**File:** chrome/browser/notifications/notification_permission_context.cc (L217-247)
```text
#if BUILDFLAG(IS_ANDROID)
  bool contains_webapk = ShortcutHelper::DoesOriginContainAnyInstalledWebApk(
      request_data->requesting_origin);
  bool contains_twa =
      ShortcutHelper::DoesOriginContainAnyInstalledTrustedWebActivity(
          request_data->requesting_origin);
  bool contains_installed_webapp = contains_twa || contains_webapk;
  if (base::android::android_info::sdk_int() >=
          base::android::android_info::SDK_VERSION_T &&
      contains_installed_webapp) {
    // WebAPKs match URLs using a scope URL which may contain a path. An origin
    // has no path and would not fall within such a scope. So to find a matching
    // WebAPK we must pass a more complete URL e.g. GetLastCommittedURL.
    InstalledWebappBridge::DecidePermission(
        ContentSettingsType::NOTIFICATIONS, request_data->requesting_origin,
        web_contents->GetLastCommittedURL(),
        base::BindOnce(&NotificationPermissionContext::NotifyPermissionSet,
                       weak_factory_ui_thread_.GetWeakPtr(),
                       permissions::PermissionRequestData(
                           request_data->id,
                           content::PermissionRequestDescription(
                               content::PermissionDescriptorUtil::
                                   CreatePermissionDescriptorForPermissionType(
                                       blink::PermissionType::NOTIFICATIONS)),
                           request_data->requesting_origin,
                           request_data->embedding_origin),
                       std::move(callback),
                       /*persist=*/false,
                       /*permission_result=*/nullptr));
    return;
  }
```

**File:** chrome/browser/geolocation/geolocation_permission_context_delegate_android.cc (L38-71)
```text
bool GeolocationPermissionContextDelegateAndroid::DecidePermission(
    const permissions::PermissionRequestData& request_data,
    permissions::BrowserPermissionCallback* callback,
    permissions::GeolocationPermissionContext* context) {
  content::RenderFrameHost* rfh = content::RenderFrameHost::FromID(
      request_data.id.global_render_frame_host_id());
  DCHECK(rfh);

  content::WebContents* web_contents =
      content::WebContents::FromRenderFrameHost(rfh);
  DCHECK(web_contents);

  if (web_contents->GetDelegate() &&
      web_contents->GetDelegate()->GetInstalledWebappGeolocationContext()) {
    ContentSettingsType type =
        content_settings::GeolocationContentSettingsType();
    CHECK_EQ(permissions::RequestTypeToContentSettingsType(
                 request_data.request_type.value())
                 .value(),
             type);
    GURL requesting_origin = request_data.requesting_origin;
    InstalledWebappBridge::PermissionCallback permission_callback =
        base::BindOnce(
            &permissions::GeolocationPermissionContext::NotifyPermissionSet,
            context->GetWeakPtr(), request_data.Clone(), std::move(*callback),
            /*persist=*/false, /*permission_result=*/nullptr);
    InstalledWebappBridge::DecidePermission(type, requesting_origin,
                                            web_contents->GetLastCommittedURL(),
                                            std::move(permission_callback));
    return true;
  }
  return GeolocationPermissionContextDelegate::DecidePermission(
      request_data, callback, context);
}
```

**File:** chrome/android/java/src/org/chromium/chrome/browser/browserservices/permissiondelegation/InstalledWebappBridge.java (L93-115)
```java
    @CalledByNative
    private static void decidePermission(
            @ContentSettingsType.EnumType int type,
            @JniType("std::string") String originUrl,
            @JniType("std::string") String lastCommittedUrl,
            long callback) {
        Origin origin = Origin.create(Uri.parse(originUrl));
        if (origin == null) {
            runPermissionCallback(callback, ContentSetting.BLOCK);
            return;
        }
        switch (type) {
            case ContentSettingsType.GEOLOCATION:
            case ContentSettingsType.GEOLOCATION_WITH_OPTIONS:
                PermissionUpdater.getLocationPermission(origin, lastCommittedUrl, callback);
                break;
            case ContentSettingsType.NOTIFICATIONS:
                PermissionUpdater.requestNotificationPermission(origin, lastCommittedUrl, callback);
                break;
            default:
                throw new IllegalStateException("Unsupported permission type.");
        }
    }
```
