package com.argon.hub

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import com.argon.hub.data.NetworkManager
import com.argon.hub.data.OtaManager
import com.argon.hub.ui.HubScreen
import com.argon.hub.ui.ServiceViewerScreen
import com.argon.hub.ui.theme.ArgonHubTheme
import kotlinx.coroutines.launch

data class ActiveService(val title: String, val url: String)

class MainActivity : ComponentActivity() {
    private val networkManager = NetworkManager()
    private val otaManager = OtaManager()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            ArgonHubTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    ArgonApp(networkManager, otaManager, getAppVersionCode())
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Auto-refresh network status and check for Pi OTA updates whenever returning to the app
        lifecycleScope.launch {
            networkManager.checkConnectivity()
            otaManager.checkForUpdates(networkManager.getActiveHost(), getAppVersionCode())
        }
    }

    private fun getAppVersionCode(): Long {
        return try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {
                packageManager.getPackageInfo(packageName, 0).longVersionCode
            } else {
                @Suppress("DEPRECATION")
                packageManager.getPackageInfo(packageName, 0).versionCode.toLong()
            }
        } catch (_: Exception) {
            1L
        }
    }
}

@Composable
fun ArgonApp(networkManager: NetworkManager, otaManager: OtaManager, currentVersionCode: Long) {
    val scope = rememberCoroutineScope()
    var activeService by remember { mutableStateOf<ActiveService?>(null) }

    val current = activeService
    if (current != null) {
        ServiceViewerScreen(
            title = current.title,
            url = current.url,
            activeHost = networkManager.getActiveHost(),
            onBack = { activeService = null },
            modifier = Modifier.fillMaxSize()
        )
    } else {
        HubScreen(
            networkManager = networkManager,
            otaManager = otaManager,
            onOpenService = { title, url ->
                activeService = ActiveService(title, url)
            },
            onRefresh = {
                scope.launch {
                    networkManager.checkConnectivity()
                    otaManager.checkForUpdates(networkManager.getActiveHost(), currentVersionCode)
                }
            },
            modifier = Modifier.fillMaxSize()
        )
    }
}
