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
import com.argon.hub.ui.HubScreen
import com.argon.hub.ui.ServiceViewerScreen
import com.argon.hub.ui.theme.ArgonHubTheme
import kotlinx.coroutines.launch

data class ActiveService(val title: String, val url: String)

class MainActivity : ComponentActivity() {
    private val networkManager = NetworkManager()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            ArgonHubTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    ArgonApp(networkManager)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Auto-refresh network status whenever user switches back to this app (e.g. from Tailscale)
        lifecycleScope.launch {
            networkManager.checkConnectivity()
        }
    }
}

@Composable
fun ArgonApp(networkManager: NetworkManager) {
    val scope = rememberCoroutineScope()
    var activeService by remember { mutableStateOf<ActiveService?>(null) }

    val current = activeService
    if (current != null) {
        ServiceViewerScreen(
            title = current.title,
            url = current.url,
            onBack = { activeService = null },
            modifier = Modifier.fillMaxSize()
        )
    } else {
        HubScreen(
            networkManager = networkManager,
            onOpenService = { title, url ->
                activeService = ActiveService(title, url)
            },
            onRefresh = {
                scope.launch {
                    networkManager.checkConnectivity()
                }
            },
            modifier = Modifier.fillMaxSize()
        )
    }
}
