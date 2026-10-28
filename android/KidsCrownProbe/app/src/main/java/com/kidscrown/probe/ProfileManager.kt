package com.kidscrown.probe

import android.content.Context
import android.content.SharedPreferences

object ProfileManager {
    private const val PREFS_NAME = "kidscrown_probe_prefs"
    private const val KEY_PROFILE_ID = "active_profile_id"
    private const val KEY_PROFILE_NAME = "active_profile_name"
    private const val KEY_EMAIL = "auth_email"
    private const val KEY_PASSWORD = "auth_password"
    private const val KEY_TOKEN = "auth_token"
    private const val KEY_BACKEND_URL = "backend_url"

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun getProfileId(context: Context): Int {
        return getPrefs(context).getInt(KEY_PROFILE_ID, -1)
    }

    fun getProfileName(context: Context): String {
        return getPrefs(context).getString(KEY_PROFILE_NAME, "") ?: ""
    }

    fun saveProfile(context: Context, id: Int, name: String) {
        getPrefs(context).edit()
            .putInt(KEY_PROFILE_ID, id)
            .putString(KEY_PROFILE_NAME, name)
            .apply()
        Probe.activeProfileId = id
        Probe.activeProfileName = name
    }

    fun getBackendUrl(context: Context): String {
        return getPrefs(context).getString(KEY_BACKEND_URL, ProbeConfig.BACKEND_URL) ?: ProbeConfig.BACKEND_URL
    }

    fun saveBackendUrl(context: Context, url: String) {
        getPrefs(context).edit().putString(KEY_BACKEND_URL, url).apply()
    }

    fun getEmail(context: Context): String {
        return getPrefs(context).getString(KEY_EMAIL, ProbeConfig.EMAIL) ?: ProbeConfig.EMAIL
    }

    fun getPassword(context: Context): String {
        return getPrefs(context).getString(KEY_PASSWORD, ProbeConfig.PASSWORD) ?: ProbeConfig.PASSWORD
    }

    fun saveAuthCredentials(context: Context, email: String, pass: String) {
        getPrefs(context).edit()
            .putString(KEY_EMAIL, email)
            .putString(KEY_PASSWORD, pass)
            .apply()
    }

    fun getToken(context: Context): String? {
        return getPrefs(context).getString(KEY_TOKEN, null)
    }

    fun saveToken(context: Context, token: String?) {
        getPrefs(context).edit().putString(KEY_TOKEN, token).apply()
    }

    fun isProfileSet(context: Context): Boolean {
        return getProfileId(context) > 0
    }

    fun init(context: Context) {
        Probe.activeProfileId = getProfileId(context)
        Probe.activeProfileName = getProfileName(context)
    }
}
