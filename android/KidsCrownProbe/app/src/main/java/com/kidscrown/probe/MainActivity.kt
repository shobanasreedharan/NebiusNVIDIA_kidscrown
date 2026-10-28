package com.kidscrown.probe

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

class MainActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var profileHeader: TextView
    private lateinit var profilesContainer: LinearLayout
    private lateinit var body: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ProfileManager.init(this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
        }

        profileHeader = TextView(this).apply {
            textSize = 16f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#1B5E20"))
            setPadding(0, 0, 0, 16)
        }

        val enable = Button(this).apply {
            text = "Enable Accessibility Service"
            setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        }

        val setupSection = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 16, 0, 16)
        }

        val setupTitle = TextView(this).apply {
            text = "Set Up Active Child Profile:"
            setTypeface(null, Typeface.BOLD)
            textSize = 14f
            setPadding(0, 8, 0, 8)
        }

        val urlEdit = EditText(this).apply {
            hint = "Backend URL"
            setText(ProfileManager.getBackendUrl(this@MainActivity))
        }
        val emailEdit = EditText(this).apply {
            hint = "Parent Email"
            setText(ProfileManager.getEmail(this@MainActivity))
        }
        val passEdit = EditText(this).apply {
            hint = "Password"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
            setText(ProfileManager.getPassword(this@MainActivity))
        }

        val fetchBtn = Button(this).apply {
            text = "Log In & Fetch Profiles"
            setOnClickListener {
                val url = urlEdit.text.toString().trim()
                val email = emailEdit.text.toString().trim()
                val pass = passEdit.text.toString().trim()
                if (url.isEmpty() || email.isEmpty() || pass.isEmpty()) {
                    Toast.makeText(this@MainActivity, "Please enter URL, email and password", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                ProfileManager.saveBackendUrl(this@MainActivity, url)
                ProfileManager.saveAuthCredentials(this@MainActivity, email, pass)
                text = "Fetching..."
                isEnabled = false

                thread {
                    try {
                        val client = BackendClient(this@MainActivity)
                        val profiles = client.fetchProfiles(url, email, pass)
                        runOnUiThread {
                            text = "Log In & Fetch Profiles"
                            isEnabled = true
                            displayProfiles(profiles)
                            Toast.makeText(this@MainActivity, "Loaded ${profiles.size} profiles", Toast.LENGTH_SHORT).show()
                        }
                    } catch (e: Exception) {
                        runOnUiThread {
                            text = "Log In & Fetch Profiles"
                            isEnabled = true
                            Toast.makeText(this@MainActivity, "Fetch error: ${e.message}", Toast.LENGTH_LONG).show()
                        }
                    }
                }
            }
        }

        profilesContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 8, 0, 8)
        }

        val manualLayout = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 8, 0, 8)
        }
        val manualIdEdit = EditText(this).apply {
            hint = "Profile ID (e.g. 2)"
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        val manualNameEdit = EditText(this).apply {
            hint = "Child Name"
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        val manualSaveBtn = Button(this).apply {
            text = "Set Manual"
            setOnClickListener {
                val idStr = manualIdEdit.text.toString().trim()
                val name = manualNameEdit.text.toString().trim().ifEmpty { "Child #$idStr" }
                val id = idStr.toIntOrNull() ?: -1
                if (id > 0) {
                    ProfileManager.saveProfile(this@MainActivity, id, name)
                    render()
                    Toast.makeText(this@MainActivity, "Active profile set to $name (ID: $id)", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(this@MainActivity, "Invalid Profile ID", Toast.LENGTH_SHORT).show()
                }
            }
        }
        manualLayout.addView(manualIdEdit)
        manualLayout.addView(manualNameEdit)
        manualLayout.addView(manualSaveBtn)

        setupSection.addView(setupTitle)
        setupSection.addView(urlEdit)
        setupSection.addView(emailEdit)
        setupSection.addView(passEdit)
        setupSection.addView(fetchBtn)
        setupSection.addView(profilesContainer)
        setupSection.addView(manualLayout)

        val refresh = Button(this).apply {
            text = "Refresh Snapshot"
            setOnClickListener { render() }
        }
        val copy = Button(this).apply {
            text = "Copy Full Dump"
            setOnClickListener {
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("kidscrown-dump", Probe.dump))
                Toast.makeText(this@MainActivity, "Copied", Toast.LENGTH_SHORT).show()
            }
        }

        status = TextView(this).apply { textSize = 13f; setPadding(0, 8, 0, 8) }
        body = TextView(this).apply { typeface = Typeface.MONOSPACE; textSize = 10f }

        val scroll = ScrollView(this).apply {
            val inner = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL
            }
            inner.addView(profileHeader)
            inner.addView(enable)
            inner.addView(setupSection)
            inner.addView(refresh)
            inner.addView(copy)
            inner.addView(status)
            inner.addView(body)
            addView(inner)
        }

        root.addView(scroll)
        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun displayProfiles(profiles: List<ProfileInfo>) {
        profilesContainer.removeAllViews()
        if (profiles.isEmpty()) {
            val emptyTv = TextView(this).apply {
                text = "No profiles found on backend."
                setTextColor(Color.RED)
            }
            profilesContainer.addView(emptyTv)
            return
        }
        val titleTv = TextView(this).apply {
            text = "Tap a profile to make this phone for that child:"
            setTypeface(null, Typeface.BOLD)
            setPadding(0, 4, 0, 4)
        }
        profilesContainer.addView(titleTv)

        for (p in profiles) {
            val btn = Button(this).apply {
                text = "Select: ${p.name} (ID: ${p.id})"
                setOnClickListener {
                    ProfileManager.saveProfile(this@MainActivity, p.id, p.name)
                    render()
                    Toast.makeText(this@MainActivity, "This phone is now for ${p.name}", Toast.LENGTH_SHORT).show()
                }
            }
            profilesContainer.addView(btn)
        }
    }

    private fun render() {
        val id = ProfileManager.getProfileId(this)
        val name = ProfileManager.getProfileName(this)

        if (id > 0) {
            profileHeader.text = "This phone is for: $name (Profile ID: $id)"
            profileHeader.setTextColor(Color.parseColor("#1B5E20"))
        } else {
            profileHeader.text = "This phone is for: [NOT SET - Select a profile below]"
            profileHeader.setTextColor(Color.RED)
        }

        status.text = buildString {
            append("YouTube detected: ").append(Probe.youtubeSeen).append('\n')
            append("Video IDs found in UI: ").append(Probe.videoIds.ifEmpty { "none" }).append('\n')
            append("Title/channel candidates:\n")
            Probe.candidates.take(6).forEach { append("  ").append(it).append('\n') }
        }
        body.text = Probe.dump
    }
}
