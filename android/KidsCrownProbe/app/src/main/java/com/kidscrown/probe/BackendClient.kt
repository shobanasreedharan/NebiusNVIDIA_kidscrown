package com.kidscrown.probe

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object ProbeConfig {
    const val BACKEND_URL = "http://127.0.0.1:8000"
    const val EMAIL = "demo@kidscrown.local"
    const val PASSWORD = "DemoPass123!"
    const val PROFILE_ID = 2

    /** How long a title must stay on screen before we call the backend. */
    const val SETTLE_MS = 1000L
}

class ProfileInfo(val id: Int, val name: String)

class BackendClient(private val context: Context? = null) {
    class Result(val http: Int, val body: String)

    @Volatile
    private var token: String? = null

    /** Blocking. Call from a background thread only. */
    fun identify(title: String, channel: String, durationSeconds: Int?, profileId: Int): Result {
        val payload = JSONObject()
            .put("profile_id", profileId)
            .put("title", title)
            .put("channel", channel)
        if (durationSeconds != null) payload.put("duration_seconds", durationSeconds)
        val json = payload.toString()

        var result = post("/identify", json, ensureToken())
        if (result.http == 401) {
            token = null
            context?.let { ProfileManager.saveToken(it, null) }
            result = post("/identify", json, ensureToken())
        }
        return result
    }

    fun fetchProfiles(baseUrl: String, email: String, pass: String): List<ProfileInfo> {
        val body = JSONObject()
            .put("email", email)
            .put("password", pass)
            .toString()
        val loginRes = requestUrl(baseUrl + "/auth/login", "POST", body, null)
        check(loginRes.http == 200) { "login failed (${loginRes.http}): ${loginRes.body}" }
        val freshToken = JSONObject(loginRes.body).getString("access_token")
        token = freshToken
        context?.let { ProfileManager.saveToken(it, freshToken) }

        return fetchProfilesWithToken(baseUrl, freshToken)
    }

    fun fetchProfilesWithToken(baseUrl: String, bearerToken: String): List<ProfileInfo> {
        var res = requestUrl(baseUrl + "/profiles", "GET", null, bearerToken)
        if (res.http != 200) {
            res = requestUrl(baseUrl + "/profiles/", "GET", null, bearerToken)
        }
        check(res.http == 200) { "fetch profiles failed (${res.http}): ${res.body}" }
        return parseProfilesJson(res.body)
    }

    private fun parseProfilesJson(jsonStr: String): List<ProfileInfo> {
        val list = mutableListOf<ProfileInfo>()
        val trimmed = jsonStr.trim()
        val jsonArray = if (trimmed.startsWith("[")) {
            org.json.JSONArray(trimmed)
        } else {
            val obj = JSONObject(trimmed)
            obj.optJSONArray("profiles") ?: obj.optJSONArray("data") ?: org.json.JSONArray()
        }
        for (i in 0 until jsonArray.length()) {
            val item = jsonArray.getJSONObject(i)
            val id = item.optInt("id", item.optInt("profile_id", -1))
            val name = item.optString("name", item.optString("display_name", "Profile #$id"))
            if (id > 0) {
                list.add(ProfileInfo(id, name))
            }
        }
        return list
    }

    private fun ensureToken(): String {
        token?.let { return it }
        val savedToken = context?.let { ProfileManager.getToken(it) }
        if (!savedToken.isNullOrBlank()) {
            token = savedToken
            return savedToken
        }
        val baseUrl = context?.let { ProfileManager.getBackendUrl(it) } ?: ProbeConfig.BACKEND_URL
        val email = context?.let { ProfileManager.getEmail(it) } ?: ProbeConfig.EMAIL
        val password = context?.let { ProfileManager.getPassword(it) } ?: ProbeConfig.PASSWORD

        val body = JSONObject()
            .put("email", email)
            .put("password", password)
            .toString()
        val res = requestUrl(baseUrl + "/auth/login", "POST", body, null)
        check(res.http == 200) { "login failed: HTTP ${res.http}" }
        val fresh = JSONObject(res.body).getString("access_token")
        token = fresh
        context?.let { ProfileManager.saveToken(it, fresh) }
        return fresh
    }

    private fun post(path: String, json: String, bearer: String?): Result {
        val baseUrl = context?.let { ProfileManager.getBackendUrl(it) } ?: ProbeConfig.BACKEND_URL
        return requestUrl(baseUrl + path, "POST", json, bearer)
    }

    private fun requestUrl(fullUrl: String, method: String, json: String?, bearer: String?): Result {
        val conn = URL(fullUrl).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 5000
            conn.readTimeout = 30000
            if (bearer != null) conn.setRequestProperty("Authorization", "Bearer $bearer")
            if (json != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(json.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            return Result(code, text)
        } finally {
            conn.disconnect()
        }
    }
}
