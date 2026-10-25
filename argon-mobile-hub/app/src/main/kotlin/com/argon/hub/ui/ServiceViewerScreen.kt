package com.argon.hub.ui

import android.annotation.SuppressLint
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.net.http.SslError
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.provider.MediaStore
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.viewinterop.AndroidView

class AndroidBridge(private val context: Context) {
    @JavascriptInterface
    fun onBlobDownload(dataUrl: String, filename: String, mimeType: String) {
        try {
            val base64 = dataUrl.substringAfter("base64,")
            val bytes = android.util.Base64.decode(base64, android.util.Base64.DEFAULT)
            val cleanName = if (filename.isNotBlank()) filename else "download_${System.currentTimeMillis()}"
            val cleanMime = if (mimeType.isNotBlank()) mimeType else "application/octet-stream"

            val resolver = context.contentResolver
            val contentValues = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, cleanName)
                put(MediaStore.MediaColumns.MIME_TYPE, cleanMime)
                put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, contentValues)
            if (uri != null) {
                resolver.openOutputStream(uri)?.use { stream ->
                    stream.write(bytes)
                }
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(context, "Saved to Downloads: $cleanName", Toast.LENGTH_LONG).show()
                }
            } else {
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(context, "Could not save file to Downloads", Toast.LENGTH_SHORT).show()
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
            Handler(Looper.getMainLooper()).post {
                Toast.makeText(context, "Download error: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }
}

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
    var currentUrl by remember { mutableStateOf(url) }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var fileUploadCallback by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }

    val fileChooserLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val intent = result.data
        val results = WebChromeClient.FileChooserParams.parseResult(result.resultCode, intent)
        fileUploadCallback?.onReceiveValue(results)
        fileUploadCallback = null
    }

    BackHandler {
        if (webViewRef?.canGoBack() == true) {
            webViewRef?.goBack()
        } else {
            onBack()
        }
    }

    Scaffold(
        topBar = {
            Column {
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
            }
        },
        modifier = modifier.fillMaxSize()
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .imePadding()
        ) {
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
                            allowFileAccess = true
                            allowContentAccess = true
                            mediaPlaybackRequiresUserGesture = false
                        }

                        addJavascriptInterface(AndroidBridge(ctx), "AndroidBridge")

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
                                // 1. Inject client-side link rewrite script for single-page apps / dashboards
                                val linkJs = """
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
                                view?.evaluateJavascript(linkJs, null)

                                // 2. Inject blob & data-url download hook for PairDrop / web file downloads
                                val blobJs = """
                                    (function() {
                                        if (window.__argonBlobHookInstalled) return;
                                        window.__argonBlobHookInstalled = true;
                                        var originalClick = HTMLAnchorElement.prototype.click;
                                        HTMLAnchorElement.prototype.click = function() {
                                            if (this.href && (this.href.startsWith('blob:') || this.href.startsWith('data:'))) {
                                                var filename = this.download || 'download_' + Date.now();
                                                fetch(this.href)
                                                    .then(function(res) { return res.blob(); })
                                                    .then(function(blob) {
                                                        var reader = new FileReader();
                                                        reader.onloadend = function() {
                                                            if (window.AndroidBridge && window.AndroidBridge.onBlobDownload) {
                                                                window.AndroidBridge.onBlobDownload(reader.result, filename, blob.type || '');
                                                            }
                                                        };
                                                        reader.readAsDataURL(blob);
                                                    })
                                                    .catch(function(err) {
                                                        console.error('Blob capture error', err);
                                                    });
                                            }
                                            return originalClick.apply(this, arguments);
                                        };
                                    })();
                                """.trimIndent()
                                view?.evaluateJavascript(blobJs, null)
                            }
                        }

                        webChromeClient = object : WebChromeClient() {
                            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                                progress = newProgress
                            }

                            override fun onPermissionRequest(request: PermissionRequest?) {
                                request?.grant(request.resources)
                            }

                            override fun onShowFileChooser(
                                view: WebView?,
                                filePathCallback: ValueCallback<Array<Uri>>?,
                                fileChooserParams: FileChooserParams?
                            ): Boolean {
                                fileUploadCallback?.onReceiveValue(null)
                                fileUploadCallback = filePathCallback
                                try {
                                    val intent = fileChooserParams?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                                        type = "*/*"
                                        putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                                    }
                                    fileChooserLauncher.launch(intent)
                                    return true
                                } catch (e: Exception) {
                                    fileUploadCallback?.onReceiveValue(null)
                                    fileUploadCallback = null
                                    return false
                                }
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
