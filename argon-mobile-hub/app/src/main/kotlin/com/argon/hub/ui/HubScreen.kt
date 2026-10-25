package com.argon.hub.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.DataObject
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.Thermostat
import androidx.compose.material.icons.filled.Update
import androidx.compose.material.icons.filled.VpnKey
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.argon.hub.data.ConnectionState
import com.argon.hub.data.NetworkManager
import com.argon.hub.data.OtaManager
import com.argon.hub.data.OtaState
import com.argon.hub.model.ServiceItem
import com.argon.hub.model.ServiceRepository
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HubScreen(
    networkManager: NetworkManager,
    otaManager: OtaManager,
    onOpenService: (title: String, url: String) -> Unit,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val connectionState by networkManager.connectionState.collectAsStateWithLifecycle()
    val otaState by otaManager.otaState.collectAsStateWithLifecycle()
    var selectedTab by remember { mutableIntStateOf(0) }

    val activeHost = when (val state = connectionState) {
        is ConnectionState.Connected -> state.host
        else -> "argon-host"
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Text(
                                text = "Argon Hub",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.Bold
                            )
                            ConnectionStatusBadge(connectionState)
                        }
                        val installedVersion = remember(context) {
                            try {
                                val pkgInfo = context.packageManager.getPackageInfo(context.packageName, 0)
                                val vName = (pkgInfo.versionName ?: "1.2").removePrefix("v")
                                val vCode = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {
                                    pkgInfo.longVersionCode
                                } else {
                                    @Suppress("DEPRECATION")
                                    pkgInfo.versionCode.toLong()
                                }
                                "$vName($vCode)"
                            } catch (_: Exception) {
                                "1.2(4)"
                            }
                        }
                        Text(
                            text = installedVersion,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                actions = {
                    IconButton(onClick = onRefresh) {
                        Icon(
                            imageVector = Icons.Default.Refresh,
                            contentDescription = "Refresh connection"
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surfaceContainer
                )
            )
        },
        modifier = modifier
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            // Offline Warning & One-Tap Tailscale Connect
            AnimatedVisibility(visible = connectionState is ConnectionState.Offline) {
                TailscalePromptCard(onConnectTailscale = { networkManager.launchTailscale(context) })
            }

            // OTA Update Notification Banner
            AnimatedVisibility(visible = otaState !is OtaState.Idle && otaState !is OtaState.Checking) {
                OtaUpdateCard(otaState = otaState, otaManager = otaManager)
            }

            PrimaryTabRow(selectedTabIndex = selectedTab) {
                Tab(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    text = { Text("Native Hub") }
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    text = { Text("Dashboard (HUD)") }
                )
            }

            if (selectedTab == 0) {
                NativeServicesList(
                    services = ServiceRepository.services,
                    activeHost = activeHost,
                    onOpenService = onOpenService
                )
            } else {
                ServiceViewerScreen(
                    title = "Argon Dashboard",
                    url = "http://$activeHost/",
                    activeHost = activeHost,
                    onBack = { selectedTab = 0 },
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
    }
}

@Composable
fun ConnectionStatusBadge(state: ConnectionState) {
    val (text, bgColor, contentColor) = when (state) {
        is ConnectionState.Checking -> Triple("Checking...", Color(0xFF64748B), Color.White)
        is ConnectionState.Connected -> {
            val label = "${state.mode} (${state.latencyMs}ms)"
            Triple(label, Color(0xFF16A34A), Color.White)
        }
        is ConnectionState.Offline -> Triple("Offline", Color(0xFFDC2626), Color.White)
    }

    Surface(
        color = bgColor,
        shape = CircleShape,
        modifier = Modifier.padding(start = 4.dp)
    ) {
        Text(
            text = text,
            color = contentColor,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
        )
    }
}

@Composable
fun TailscalePromptCard(onConnectTailscale: () -> Unit) {
    ElevatedCard(
        colors = CardDefaults.elevatedCardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer
        ),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = Icons.Default.Warning,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.error
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "Argon Pi Unreachable",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onErrorContainer
                )
            }
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "If you are away from home Wi-Fi, open the Tailscale app to connect your private mesh.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onErrorContainer
            )
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = onConnectTailscale,
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error
                )
            ) {
                Icon(Icons.Default.VpnKey, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("Open Tailscale App")
            }
        }
    }
}

@Composable
fun NativeServicesList(
    services: List<ServiceItem>,
    activeHost: String,
    onOpenService: (title: String, url: String) -> Unit
) {
    val context = LocalContext.current
    var infoService by remember { mutableStateOf<ServiceItem?>(null) }
    val categories = services.groupBy { it.category }

    if (infoService != null) {
        val s = infoService!!
        val infoText = if (s.id == "watchtower") {
            "Watchtower is an automated background daemon that monitors all 19 Docker containers on your Raspberry Pi.\n\n" +
            "Every night at 4:00 AM, it checks for updated container images, gracefully pulls them, and sends a notification directly to your Telegram bot.\n\n" +
            "It also cleans up old unused image layers to preserve microSD disk space."
        } else {
            "Cowrie is an active SSH & Telnet decoy honeypot running on ports 2222 and 2223.\n\n" +
            "It silently captures attacker intrusion sessions in the background and delivers real-time threat intelligence directly to your Telegram bot.\n\n" +
            "Because it is a deception sensor, it runs purely in the background without an HTTP web dashboard."
        }
        AlertDialog(
            onDismissRequest = { infoService = null },
            title = { Text(s.title) },
            text = { Text(infoText) },
            confirmButton = {
                TextButton(onClick = { infoService = null }) {
                    Text("Understood")
                }
            }
        )
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        categories.forEach { (category, items) ->
            item {
                Text(
                    text = category,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(start = 16.dp, top = 16.dp, bottom = 4.dp)
                )
            }

            items(items) { service ->
                val fullUrl = service.getUrl(activeHost)
                ServiceCard(
                    service = service,
                    fullUrl = fullUrl,
                    onClick = {
                        if (!service.isWebUi) {
                            infoService = service
                        } else if (service.externalUrl != null) {
                            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(service.externalUrl)).apply {
                                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            }
                            context.startActivity(intent)
                        } else {
                            onOpenService(service.title, fullUrl)
                        }
                    }
                )
            }
        }
        item { Spacer(modifier = Modifier.height(24.dp)) }
    }
}

@Composable
fun ServiceCard(
    service: ServiceItem,
    fullUrl: String,
    onClick: () -> Unit
) {
    ElevatedCard(
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp)
            .clickable(onClick = onClick)
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Surface(
                shape = RoundedCornerShape(10.dp),
                color = MaterialTheme.colorScheme.primaryContainer,
                modifier = Modifier.size(46.dp)
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(
                        imageVector = getIconForService(service.iconName),
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(26.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.width(16.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = service.title,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold
                )
                Text(
                    text = service.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            Spacer(modifier = Modifier.width(8.dp))

            Surface(
                shape = CircleShape,
                color = MaterialTheme.colorScheme.surfaceContainerHigh
            ) {
                val label = if (service.port > 0) ":${service.port}" else "Bot"
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                )
            }
        }
    }
}

fun getIconForService(name: String): ImageVector = when (name) {
    "security" -> Icons.Default.Dns
    "lock" -> Icons.Default.Lock
    "terminal" -> Icons.Default.Terminal
    "shield" -> Icons.Default.Shield
    "database" -> Icons.Default.DataObject
    "web" -> Icons.Default.Language
    "file" -> Icons.Default.Description
    "speed" -> Icons.Default.Speed
    "sync" -> Icons.Default.Sync
    "layers" -> Icons.Default.Layers
    "send" -> Icons.AutoMirrored.Filled.Send
    "build" -> Icons.Default.Build
    "thermostat" -> Icons.Default.Thermostat
    "update" -> Icons.Default.Update
    "share" -> Icons.AutoMirrored.Filled.Send
    else -> Icons.AutoMirrored.Filled.OpenInNew
}

@Composable
fun OtaUpdateCard(otaState: OtaState, otaManager: OtaManager) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    when (otaState) {
        is OtaState.Available -> {
            ElevatedCard(
                colors = CardDefaults.elevatedCardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                ),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = Icons.Default.SystemUpdate,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = "New Update: v${otaState.info.versionName} (Build ${otaState.info.versionCode})",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onPrimaryContainer
                        )
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = otaState.info.releaseNotes,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Button(onClick = {
                            scope.launch {
                                otaManager.downloadAndInstall(context, otaState.downloadUrl)
                            }
                        }) {
                            Text("Install Now")
                        }
                        OutlinedButton(onClick = {
                            otaManager.openBrowserDownload(context, otaState.downloadUrl)
                        }) {
                            Text("Browser")
                        }
                        TextButton(onClick = { otaManager.dismissUpdate() }) {
                            Text("Later")
                        }
                    }
                }
            }
        }
        is OtaState.Downloading -> {
            ElevatedCard(
                colors = CardDefaults.elevatedCardColors(
                    containerColor = MaterialTheme.colorScheme.secondaryContainer
                ),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Downloading Update: ${otaState.progressPercent}%",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    LinearProgressIndicator(
                        progress = { otaState.progressPercent / 100f },
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            }
        }
        is OtaState.ReadyToInstall -> {
            ElevatedCard(
                colors = CardDefaults.elevatedCardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                ),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Row(
                    modifier = Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "Update Ready to Install",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold
                        )
                        Text(
                            text = "Tap Install to apply the update.",
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                    Button(onClick = { otaManager.installApk(context, otaState.file) }) {
                        Text("Install")
                    }
                }
            }
        }
        is OtaState.Failed -> {
            ElevatedCard(
                colors = CardDefaults.elevatedCardColors(
                    containerColor = MaterialTheme.colorScheme.errorContainer
                ),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Row(
                    modifier = Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Update error: ${otaState.reason}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        modifier = Modifier.weight(1f)
                    )
                    TextButton(onClick = { otaManager.dismissUpdate() }) {
                        Text("Dismiss")
                    }
                }
            }
        }
        else -> Unit
    }
}
