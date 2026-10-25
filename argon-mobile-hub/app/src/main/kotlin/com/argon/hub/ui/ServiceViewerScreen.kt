package com.argon.hub.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.net.http.SslError
import android.os.Message
import android.view.ViewGroup
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.viewinterop.AndroidView

@OptIn(ExperimentalMaterial3Api::class)
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun ServiceViewerScreen(
    title: String,
    url: String,
    activeHost: String = "",
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    var progress by remember { mutableIntStateOf(0) }
    var currentUrl by remember { androidx.compose.runtime.mutableStateOf(url) }
    var webViewRef by remember { androidx.compose.runtime.mutableStateOf<WebView?>(null) }

    BackHandler {
        if (webViewRef?.canGoBack() == true) {
            webViewRef?.goBack()
        } else {
            onBack()
        }
    }

    Column(modifier = modifier.fillMaxSize()) {
        TopAppBar(
            title = {
                Column {
                    Text(
                        text = title,
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        text = currentUrl,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "Back to Hub"
                    )
                }
            },
            actions = {
                IconButton(onClick = { webViewRef?.reload() }) {
                    Icon(
                        imageVector = Icons.Default.Refresh,
                        contentDescription = "Reload"
                    )
                }
                IconButton(onClick = {
                    val browserIntent = Intent(Intent.ACTION_VIEW, Uri.parse(currentUrl)).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    context.startActivity(browserIntent)
                }) {
                    Icon(
                        imageVector = Icons.Default.OpenInBrowser,
                        contentDescription = "Open in External Browser"
                    )
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surfaceContainer
            )
        )

        if (progress in 1..99) {
            LinearProgressIndicator(
                progress = { progress / 100f },
                modifier = Modifier.fillMaxWidth()
            )
        }

        Box(modifier = Modifier.weight(1f)) {
            AndroidView(
                factory = { ctx ->
                    WebView(ctx).apply {
                        layoutParams = ViewGroup.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT
                        )
                        settings.apply {
                            javaScriptEnabled = true
                            domStorageEnabled = true
                            useWideViewPort = true
                            loadWithOverviewMode = true
                            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                            cacheMode = WebSettings.LOAD_DEFAULT
                            setSupportMultipleWindows(true)
                            javaScriptCanOpenWindowsAutomatically = true
                        }

                        val targetHost = if (activeHost.isNotBlank()) activeHost else "192.168.1.10"

                        fun rewriteUrlIfNeeded(inputUri: Uri): String {
                            val host = inputUri.host ?: return inputUri.toString()
                            if (host == "argon-host" || host == "argon-host.local") {
                                val portSuffix = if (inputUri.port != -1) ":${inputUri.port}" else ""
                                val pathPart = inputUri.encodedPath ?: ""
                                val queryPart = if (inputUri.encodedQuery != null) "?${inputUri.encodedQuery}" else ""
                                return "${inputUri.scheme}://$targetHost$portSuffix$pathPart$queryPart"
                            }
                            return inputUri.toString()
                        }

                        webViewClient = object : WebViewClient() {
                            override fun onReceivedSslError(
                                view: WebView?,
                                handler: SslErrorHandler?,
                                error: SslError?
                            ) {
                                // Gracefully proceed with self-signed SSL on local/Tailscale
                                handler?.proceed()
                            }

                            override fun shouldOverrideUrlLoading(
                                view: WebView?,
                                request: WebResourceRequest?
                            ): Boolean {
                                val uri = request?.url ?: return false
                                val rewritten = rewriteUrlIfNeeded(uri)
                                if (rewritten != uri.toString()) {
                                    view?.loadUrl(rewritten)
                                    return true
                                }
                                return false
                            }

                            override fun onPageFinished(view: WebView?, finishedUrl: String?) {
                                super.onPageFinished(view, finishedUrl)
                                if (finishedUrl != null) {
                                    currentUrl = finishedUrl
                                }
                                // Inject client-side link rewrite script for single-page apps / dashboards
                                val js = """
                                    (function() {
                                        var target = '$targetHost';
                                        function rewriteLinks() {
                                            var links = document.querySelectorAll('a[href*="argon-host"]');
                                            for (var i = 0; i < links.length; i++) {
                                                links[i].href = links[i].href.replace(/argon-host(\.local)?/g, target);
                                                links[i].removeAttribute('target');
                                            }
                                        }
                                        rewriteLinks();
                                        setTimeout(rewriteLinks, 400);
                                        setTimeout(rewriteLinks, 1200);
                                        setTimeout(rewriteLinks, 3000);
                                    })();
                                """.trimIndent()
                                view?.evaluateJavascript(js, null)
                            }
                        }

                        webChromeClient = object : WebChromeClient() {
                            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                                progress = newProgress
                            }

                            override fun onCreateWindow(
                                view: WebView?,
                                isDialog: Boolean,
                                isUserGesture: Boolean,
                                resultMsg: Message?
                            ): Boolean {
                                val tempWebView = WebView(view!!.context)
                                tempWebView.webViewClient = object : WebViewClient() {
                                    override fun shouldOverrideUrlLoading(
                                        v: WebView?,
                                        req: WebResourceRequest?
                                    ): Boolean {
                                        val reqUri = req?.url ?: return false
                                        val rewritten = rewriteUrlIfNeeded(reqUri)
                                        view.loadUrl(rewritten)
                                        return true
                                    }
                                }
                                val transport = resultMsg?.obj as? WebView.WebViewTransport
                                transport?.webView = tempWebView
                                resultMsg?.sendToTarget()
                                return true
                            }
                        }
                        webViewRef = this
                        loadUrl(url)
                    }
                },
                update = { view ->
                    if (view.url != url) {
                        view.loadUrl(url)
                    }
                },
                modifier = Modifier.fillMaxSize()
            )
        }
    }
}
