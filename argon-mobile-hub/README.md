# Argon Mobile Hub (Android)

Native Kotlin & Jetpack Compose Android client for the Raspberry Pi homelab (`argon-host`).

## Key Features

1. **Auto-Endpoint Routing (Zero Pi Changes Required)**:
   - Prioritizes Home Wi-Fi (`192.168.1.10`) for lowest latency when at home.
   - Automatically fails over to Tailscale (`100.123.87.122` / `argon-host`) when off-network.
   - Detects offline / disconnected state with an immediate **1-Tap Tailscale Connect** button.
   - Auto-reprobes network upon returning to the app (`onResume`).

2. **Native Material Design 3 Experience**:
   - Modern dark UI matching Argon homelab styling.
   - Categorized card grid for all 11 homelab services:
     - **Homepage Dashboard** (Port 80)
     - **AdGuard Home** (Port 8080)
     - **Uptime Kuma** (Port 3001)
     - **Vaultwarden Password Vault** (Port 8443, HTTPS with internal TLS trust)
     - **Stirling-PDF** (Port 8086)
     - **Syncthing** (Port 8384)
     - **Docker Registry UI** (Port 5001)
     - **Chromium Sandbox / Neeko** (Port 3002)
     - **Qdrant Vector DB** (Port 6333)
     - **CrowdSec Bouncer** (Port 8081)
     - **Cowrie Honeypot** (Port 2222)
   - Dual Modes:
     - **Native Hub**: Individual service cards with launch & external browser shortcuts.
     - **Dashboard (HUD)**: Embedded full Homepage dashboard directly in-app.

3. **In-App Service Viewer**:
   - Interactive `WebView` with hardware acceleration.
   - SSL bypass for internal homelab self-signed certificates (e.g. Vaultwarden Caddy TLS).
   - In-app progress indicator, page reload, and external Chrome launcher.

## Building the APK

```bash
cd argon-mobile-hub
JAVA_HOME=$HOME/.local/share/jdk-17/Contents/Home ANDROID_HOME=$HOME/Library/Android/sdk ./gradlew assembleDebug
```

The compiled APK will be located at:
`app/build/outputs/apk/debug/app-debug.apk`
