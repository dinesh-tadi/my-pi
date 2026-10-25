package com.argon.hub.data

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

data class OtaInfo(
    val versionCode: Int,
    val versionName: String,
    val releaseDate: String,
    val apkPath: String,
    val releaseNotes: String
)

sealed class OtaState {
    data object Idle : OtaState()
    data object Checking : OtaState()
    data class Available(val info: OtaInfo, val downloadUrl: String) : OtaState()
    data class Downloading(val progressPercent: Int) : OtaState()
    data class ReadyToInstall(val file: File) : OtaState()
    data class Failed(val reason: String) : OtaState()
}

class OtaManager {
    private val _otaState = MutableStateFlow<OtaState>(OtaState.Idle)
    val otaState: StateFlow<OtaState> = _otaState.asStateFlow()

    fun dismissUpdate() {
        _otaState.value = OtaState.Idle
    }

    suspend fun checkForUpdates(activeHost: String, currentVersionCode: Long) = withContext(Dispatchers.IO) {
        if (activeHost.isBlank()) return@withContext
        _otaState.value = OtaState.Checking

        try {
            val url = URL("http://$activeHost:5050/version.json")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 1800
                readTimeout = 1800
                requestMethod = "GET"
                setRequestProperty("Accept", "application/json")
            }

            if (conn.responseCode == 200) {
                val jsonString = conn.inputStream.bufferedReader().use { it.readText() }
                val obj = JSONObject(jsonString)
                val remoteCode = obj.getInt("versionCode")
                val remoteName = obj.optString("versionName", "New Version")
                val releaseDate = obj.optString("releaseDate", "")
                val apkPath = obj.optString("apkPath", "/argon-hub.apk")
                val releaseNotes = obj.optString("releaseNotes", "New update available from Raspberry Pi.")

                if (remoteCode > currentVersionCode) {
                    val info = OtaInfo(
                        versionCode = remoteCode,
                        versionName = remoteName,
                        releaseDate = releaseDate,
                        apkPath = apkPath,
                        releaseNotes = releaseNotes
                    )
                    val fullDownloadUrl = "http://$activeHost:5050$apkPath"
                    _otaState.value = OtaState.Available(info, fullDownloadUrl)
                } else {
                    _otaState.value = OtaState.Idle
                }
            } else {
                _otaState.value = OtaState.Idle
            }
        } catch (_: Exception) {
            // Server offline or not reachable; stay silent
            _otaState.value = OtaState.Idle
        }
    }

    suspend fun downloadAndInstall(context: Context, downloadUrl: String) = withContext(Dispatchers.IO) {
        _otaState.value = OtaState.Downloading(0)
        try {
            val url = URL(downloadUrl)
            val conn = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 5000
                readTimeout = 30000
                requestMethod = "GET"
            }

            val fileLength = conn.contentLength
            val destFile = File(context.cacheDir, "argon-hub-update.apk")
            if (destFile.exists()) {
                destFile.delete()
            }

            conn.inputStream.use { input ->
                FileOutputStream(destFile).use { output ->
                    val buffer = ByteArray(8192)
                    var totalRead: Long = 0
                    var bytesRead: Int
                    while (input.read(buffer).also { bytesRead = it } != -1) {
                        output.write(buffer, 0, bytesRead)
                        totalRead += bytesRead
                        if (fileLength > 0) {
                            val percent = ((totalRead * 100) / fileLength).toInt()
                            _otaState.value = OtaState.Downloading(percent)
                        }
                    }
                }
            }

            _otaState.value = OtaState.ReadyToInstall(destFile)
            withContext(Dispatchers.Main) {
                installApk(context, destFile)
            }
        } catch (e: Exception) {
            _otaState.value = OtaState.Failed(e.localizedMessage ?: "Download failed")
        }
    }

    fun installApk(context: Context, file: File) {
        try {
            val apkUri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                file
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(apkUri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
        } catch (e: Exception) {
            _otaState.value = OtaState.Failed("Installer launch failed: ${e.message}")
        }
    }

    fun openBrowserDownload(context: Context, downloadUrl: String) {
        try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(downloadUrl)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
        } catch (_: Exception) {}
    }
}
