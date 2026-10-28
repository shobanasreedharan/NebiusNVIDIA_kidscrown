package com.kidscrown.probe

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject
import java.util.concurrent.Executors

/** Latest snapshot, shared with MainActivity for display. */
object Probe {
    @Volatile var youtubeSeen = false
    @Volatile var lastSeenAt = 0L
    @Volatile var videoIds = setOf<String>()
    @Volatile var candidates = listOf<String>()
    @Volatile var dump = "No YouTube events yet. Open YouTube and tap a video."
    @Volatile var activeProfileId: Int = -1
    @Volatile var activeProfileName: String = ""
}

/** What one tree walk managed to read. */
private class VideoInfo {
    var title: String? = null
    var handle: String? = null
    var channelName: String? = null
    var durationSec: Int? = null
    var currentSec: Int? = null
    var shorts = false
    val markers = sortedSetOf<String>()
    val textNodes = mutableListOf<String>()
}

private class KidsNodeInfo(
    val depth: Int,
    val shortClass: String,
    val id: String?,
    val text: String?,
    val desc: String?,
    val isClickable: Boolean
)

private class KidsScreenSnapshot {
    var playerPresent = false
    var loadingSpinnerPresent = false
    var titleBarPresent = false
    var timeBarPresent = false
    var playerDesc: String? = null
    var title: String? = null
    var channel: String? = null
    var durationSec: Int? = null

    val controlsVisible: Boolean
        get() = titleBarPresent || timeBarPresent
}

/** The video we consider "playing now". Survives fullscreen and the miniplayer. */
private class CurrentVideo(val title: String, val firstSeen: Long) {
    var handle: String? = null
    var channel: String? = null
    var durationSec: Int? = null
    @Volatile var status: String? = null // allow | ask_parent | block, once decided
    @Volatile var sent = false
    @Volatile var retryAt = 0L
    @Volatile var attempts = 0
}

class KidsCrownService : AccessibilityService() {

    private val backend by lazy { BackendClient(this) }
    private val worker = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile private var currentVideo: CurrentVideo? = null
    private var lastDumpAt = 0L
    private var lastLogged = ""
    private var lastShortsHash = 0
    private var lastKidsDumpHash = 0
    private var lastKidsProcessAt = 0L
    private var kidsPlayerSince = 0L
    private var kidsPlayerAbsentSince = 0L
    private var kidsTitleWaitSince = 0L
    private var kidsTitleWaitLogged = false
    private var kidsHadPlayer = false
    private var kidsSpinnerTickCount = 0
    private var kidsSpinnerReset = false
    private var kidsRevealCount = 0
    private var kidsNextRevealTime = 0L
    private var kidsDiagLogged = false
    private var kidsGoBackUntil = 0L
    private var kidsLastIdentified = ""
    private var kidsLastObservedTitle: String? = null
    private var kidsLastState = ""

    private var currentAppPkg: String? = null
    private var lastScreen: String = "OTHER"
    private var otherScreenSince = 0L
    private var unknownPlayerSince = 0L
    private val decisionCache = mutableMapOf<String, String>()
    private var pausedByUs = false
    private var savedMusicVolume: Int? = null
    private var audioFocusRequest: AudioFocusRequest? = null

    private val overlay by lazy {
        EnforcementOverlay(this) {
            performGoBack(1)
        }
    }

    private var watchdogRunning = false
    private var rootNullSince = 0L

    private val watchdogRunnable = object : Runnable {
        override fun run() {
            if (overlay.current == null) {
                watchdogRunning = false
                rootNullSince = 0L
                return
            }

            val root = rootInActiveWindow
            if (root != null) {
                rootNullSince = 0L
                val pkg = root.packageName?.toString()
                if (pkg != null && pkg !in SUPPORTED_PACKAGES && !isTransient(pkg)) {
                    Log.i(TAG, "WATCHDOG hide left_youtube pkg=$pkg")
                    overlay.hide("watchdog_left_youtube")
                    restorePlayback(false)
                    watchdogRunning = false
                    if (!shouldRunTicker()) stopTicker()
                    return
                }
            } else {
                val now = System.currentTimeMillis()
                if (rootNullSince == 0L) {
                    rootNullSince = now
                } else if (now - rootNullSince > 10_000) {
                    Log.i(TAG, "WATCHDOG hide root_null_timeout")
                    overlay.hide("watchdog_root_null_timeout")
                    restorePlayback(false)
                    watchdogRunning = false
                    rootNullSince = 0L
                    if (!shouldRunTicker()) stopTicker()
                    return
                }
            }

            mainHandler.postDelayed(this, 1000)
        }
    }

    private fun shouldRunTicker(): Boolean {
        return overlay.current != null ||
            (currentVideo != null && currentVideo?.status == null) ||
            (currentVideo == null && (lastScreen == "PLAYER_NO_TITLE" || lastScreen == "MINIPLAYER")) ||
            (KIDS_ENFORCEMENT_ENABLED && currentAppPkg == YOUTUBE_KIDS &&
                (lastScreen == "KIDS_PLAYER" || kidsPlayerAbsentSince != 0L || System.currentTimeMillis() < kidsGoBackUntil))
    }

    private var tickerRunning = false

    private val tickerRunnable = object : Runnable {
        override fun run() {
            if (!shouldRunTicker()) {
                tickerRunning = false
                return
            }
            val now = System.currentTimeMillis()
            if (now - lastDumpAt >= 1000) {
                if (currentAppPkg == YOUTUBE_KIDS && KIDS_ENFORCEMENT_ENABLED) {
                    if (now - lastKidsProcessAt >= 1000) {
                        lastDumpAt = now
                        lastKidsProcessAt = now
                        val root = rootInActiveWindow
                        if (root?.packageName?.toString() == YOUTUBE_KIDS) {
                            processKidsWindow(root, now)
                        }
                    }
                } else {
                    lastDumpAt = now
                    processWindow(now)
                }
            }
            if (shouldRunTicker()) {
                mainHandler.postDelayed(this, 1000)
            } else {
                tickerRunning = false
            }
        }
    }

    private fun startTickerIfNeeded() {
        if (!tickerRunning && shouldRunTicker()) {
            tickerRunning = true
            mainHandler.postDelayed(tickerRunnable, 1000)
        }
    }

    private fun stopTicker() {
        if (tickerRunning) {
            mainHandler.removeCallbacks(tickerRunnable)
            tickerRunning = false
        }
    }

    private val markerWords =
        listOf("watch_player", "watch_list", "reel", "shorts", "search", "mini", "results")

    private val urlRegex =
        Regex("""(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/))([A-Za-z0-9_-]{11})""")
    // e.g. "MrBeast 520M subscribers"
    private val subsRegex =
        Regex("""^(.+?)\s+[\d.,]+[KMB]?\s+subscribers?$""", RegexOption.IGNORE_CASE)
    private val clockRegex =
        Regex("""^\d{1,2}:\d{2}(?::\d{2})?$""")
    private val kidsDurationRegex =
        Regex("""(?:(\d+)\s+hours?)?\s*(?:(\d+)\s+minutes?)?\s*(?:(\d+)\s+seconds?)?""", RegexOption.IGNORE_CASE)

    private fun isValidTitle(s: String): Boolean {
        val trimmed = s.trim()
        if (trimmed.length < 2) return false
        if (clockRegex.matches(trimmed)) return false
        return !trimmed.equals("Sponsored", ignoreCase = true) &&
            !trimmed.equals("Ad", ignoreCase = true) &&
            !trimmed.equals("Advertisement", ignoreCase = true)
    }

    override fun onCreate() {
        super.onCreate()
        ProfileManager.init(this)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString()

        if (pkg !in SUPPORTED_PACKAGES) {
            // Leaving supported apps ends the current video. Keyboards, the status bar and
            // similar overlays also fire window events, so they are ignored.
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
                pkg != null && !isTransient(pkg) &&
                (currentVideo != null || currentAppPkg == YOUTUBE_KIDS)
            ) {
                if (currentAppPkg == YOUTUBE_KIDS) {
                    resetKidsTracking("left_kids_app")
                    overlay.hide("left_kids_app")
                    restorePlayback(false)
                    stopWatchdog()
                }
                Log.i(TAG, "RESET (left YouTube for $pkg)")
                currentVideo = null
                lastScreen = "OTHER"
                otherScreenSince = 0L
                currentAppPkg = null
                refreshOverlay()
            }
            return
        }

        // Switched between supported apps?
        if (currentAppPkg != null && currentAppPkg != pkg) {
            if (currentAppPkg == YOUTUBE_KIDS || pkg == YOUTUBE_KIDS) {
                resetKidsTracking("app_switch")
            }
            Log.i(TAG, "RESET (switched app to $pkg)")
            currentVideo = null
            lastScreen = "OTHER"
            otherScreenSince = 0L
            unknownPlayerSince = 0L
            overlay.hide("switched_app")
            restorePlayback(false)
        }
        currentAppPkg = pkg

        if (pkg == YOUTUBE_KIDS && !KIDS_ENFORCEMENT_ENABLED) {
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                Log.i(TAG, "KIDS window class=${event.className}")
                try {
                    val winCount = windows?.size
                    if (winCount != null) {
                        Log.i(TAG, "KIDS windows count=$winCount")
                    }
                } catch (_: Exception) {}
            }

            val now = System.currentTimeMillis()
            if (now - lastDumpAt < 1000) return
            lastDumpAt = now

            val root = rootInActiveWindow ?: return
            if (root.packageName?.toString() == YOUTUBE_KIDS) {
                val shortIds = sortedSetOf<String>()
                val nodes = mutableListOf<KidsNodeInfo>()
                walkKids(root, 0, shortIds, nodes, KidsScreenSnapshot())
            }
            return
        }

        if (pkg == YOUTUBE_KIDS) {
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                Probe.youtubeSeen = true
                Probe.lastSeenAt = System.currentTimeMillis()
            }
            val now = System.currentTimeMillis()
            if (now - lastKidsProcessAt < 1000) return
            lastKidsProcessAt = now
            val root = rootInActiveWindow ?: return
            if (root.packageName?.toString() == YOUTUBE_KIDS) {
                processKidsWindow(root, now)
                startTickerIfNeeded()
            }
            return
        }

        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            Probe.youtubeSeen = true
            Probe.lastSeenAt = System.currentTimeMillis()
        }

        // Throttle to one tree walk per second.
        val now = System.currentTimeMillis()
        if (now - lastDumpAt < 1000) return
        lastDumpAt = now

        processWindow(now)
    }

    private fun processKidsWindow(root: AccessibilityNodeInfo, now: Long) {
        if (root.packageName?.toString() != YOUTUBE_KIDS) return
        val shortIds = sortedSetOf<String>()
        val nodes = mutableListOf<KidsNodeInfo>()
        val snapshot = KidsScreenSnapshot()

        walkKids(root, 0, shortIds, nodes, snapshot)

        if (DUMP_KIDS_NODES) {
            val hash = nodes.hashCode()
            if (hash != lastKidsDumpHash) {
                lastKidsDumpHash = hash
                for (node in nodes.take(200)) {
                    Log.i(
                        TAG,
                        "KIDS node depth=${node.depth} class=${node.shortClass} " +
                            "id=${node.id} text=${node.text} desc=${node.desc} " +
                            "clickable=${node.isClickable}"
                    )
                }
            }
        }

        if (snapshot.playerPresent) {
            kidsPlayerAbsentSince = 0L
            if (!kidsHadPlayer) {
                kidsHadPlayer = true
                kidsPlayerSince = now
                kidsRevealCount = 0
                kidsNextRevealTime = now + KIDS_REVEAL_DELAY_MS
                kidsDiagLogged = false
                if (now >= kidsGoBackUntil) {
                    kidsTitleWaitSince = now
                    kidsTitleWaitLogged = false
                }
            }
            lastScreen = "KIDS_PLAYER"

            if (currentVideo == null && !kidsDiagLogged && now - kidsPlayerSince >= 5000) {
                kidsDiagLogged = true
                Log.i(
                    TAG,
                    "KIDS no_title_diag ids=[${shortIds.joinToString(", ")}] " +
                        "player_desc=${snapshot.playerDesc ?: "none"} " +
                        "controls_visible=${snapshot.controlsVisible}"
                )
            }

            if (snapshot.loadingSpinnerPresent) {
                kidsSpinnerTickCount++
                if (kidsSpinnerTickCount >= 2 && !kidsSpinnerReset) {
                    resetKidsVideo("loading_spinner", resetTitleWait = true)
                    kidsSpinnerReset = true
                }
            } else {
                kidsSpinnerTickCount = 0
                kidsSpinnerReset = false
            }

            val title = snapshot.title
            val channel = snapshot.channel
            if (!title.isNullOrBlank()) {
                if (kidsLastObservedTitle != null && kidsLastObservedTitle != title) {
                    resetKidsVideo("title_changed", resetTitleWait = false)
                    kidsRevealCount = 0
                    kidsNextRevealTime = now + KIDS_REVEAL_DELAY_MS
                }
                kidsLastObservedTitle = title
            }
            if (!title.isNullOrBlank() && !channel.isNullOrBlank()) {
                var video = currentVideo
                if (video == null) {
                    video = CurrentVideo(title, now)
                    decisionCache[title]?.let { cachedStatus ->
                        video.status = cachedStatus
                    }
                    currentVideo = video
                    kidsTitleWaitSince = 0L
                    kidsTitleWaitLogged = false
                    startTickerIfNeeded()
                }
                video.channel = channel
                snapshot.durationSec?.let { duration ->
                    if (duration > (video.durationSec ?: 0)) video.durationSec = duration
                }
                val identifiedKey = "$title\u0000$channel"
                if (identifiedKey != kidsLastIdentified) {
                    kidsLastIdentified = identifiedKey
                    Log.i(TAG, "KIDS identified title=$title channel=$channel durationSec=${video.durationSec ?: "unknown"}")
                }
            }

            maybeTapKidsControls(root, snapshot, now)
        } else {
            kidsSpinnerTickCount = 0
            kidsSpinnerReset = false
            if (kidsHadPlayer) {
                if (kidsPlayerAbsentSince == 0L) kidsPlayerAbsentSince = now
                if (now - kidsPlayerAbsentSince >= 1000) {
                    resetKidsTracking("player_absent")
                    lastScreen = "KIDS_OTHER"
                } else {
                    // Keep the current decision and overlay during the one-second transition.
                    lastScreen = "KIDS_PLAYER"
                }
            } else {
                lastScreen = "KIDS_OTHER"
            }
        }

        maybeSend(now)
        refreshOverlay()
    }

    private fun walkKids(
        node: AccessibilityNodeInfo,
        depth: Int,
        shortIds: MutableSet<String>,
        nodes: MutableList<KidsNodeInfo>,
        snapshot: KidsScreenSnapshot
    ) {
        if (depth > 40) return
        val text = node.text?.toString()
        val desc = node.contentDescription?.toString()
        val shortId = node.viewIdResourceName?.substringAfter(":id/")
        val shortClass = node.className?.toString()?.substringAfterLast('.') ?: "View"
        val isClickable = node.isClickable

        if (shortId != null) {
            shortIds.add(shortId)
            if (shortId.endsWith("watch_player")) {
                snapshot.playerPresent = true
                if (node.contentDescription != null) {
                    snapshot.playerDesc = node.contentDescription.toString()
                }
            }
            if (shortId.endsWith("loading_spinner")) snapshot.loadingSpinnerPresent = true
            if (shortId.endsWith("title_bar") || shortId.endsWith("header_title")) snapshot.titleBarPresent = true
            if (shortId.endsWith("time_bar")) snapshot.timeBarPresent = true
            if (shortId.endsWith("header_title") && !text.isNullOrBlank()) snapshot.title = text
            if (shortId.endsWith("header_channel_info") && !text.isNullOrBlank()) snapshot.channel = text
            if (shortId.endsWith("time_bar")) snapshot.durationSec = parseKidsDuration(desc)
        }

        if (!text.isNullOrBlank() || !desc.isNullOrBlank() || shortId != null) {
            if (nodes.size < 200) {
                nodes.add(
                    KidsNodeInfo(
                        depth = depth,
                        shortClass = shortClass,
                        id = shortId,
                        text = text,
                        desc = desc,
                        isClickable = isClickable
                    )
                )
            }
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            walkKids(child, depth + 1, shortIds, nodes, snapshot)
        }
    }

    private fun resetKidsVideo(reason: String, resetTitleWait: Boolean) {
        Log.i(TAG, "KIDS RESET reason=$reason")
        currentVideo = null
        kidsLastIdentified = ""
        kidsRevealCount = 0
        kidsNextRevealTime = System.currentTimeMillis() + KIDS_REVEAL_DELAY_MS
        kidsDiagLogged = false
        if (resetTitleWait) {
            kidsTitleWaitSince = System.currentTimeMillis()
            kidsTitleWaitLogged = false
        }
    }

    private fun resetKidsTracking(reason: String) {
        Log.i(TAG, "KIDS RESET reason=$reason")
        currentVideo = null
        kidsPlayerSince = 0L
        kidsPlayerAbsentSince = 0L
        kidsTitleWaitSince = 0L
        kidsTitleWaitLogged = false
        kidsHadPlayer = false
        kidsSpinnerTickCount = 0
        kidsSpinnerReset = false
        kidsRevealCount = 0
        kidsNextRevealTime = 0L
        kidsDiagLogged = false
        kidsLastIdentified = ""
        kidsLastObservedTitle = null
        kidsLastState = ""
        lastScreen = "KIDS_OTHER"
    }

    private fun parseKidsDuration(description: String?): Int? {
        if (description.isNullOrBlank()) return null
        val durationText = description.substringAfter(" of ", "").substringBefore(",").trim()
        if (durationText.isEmpty()) return null
        val match = kidsDurationRegex.matchEntire(durationText) ?: return null
        if ((1..3).all { match.groups[it] == null }) return null
        val hours = match.groups[1]?.value?.toLongOrNull() ?: 0L
        val minutes = match.groups[2]?.value?.toLongOrNull() ?: 0L
        val seconds = match.groups[3]?.value?.toLongOrNull() ?: 0L
        val total = hours * 3600 + minutes * 60 + seconds
        return total.takeIf { it in 1..Int.MAX_VALUE.toLong() }?.toInt()
    }

    private fun maybeTapKidsControls(root: AccessibilityNodeInfo, snapshot: KidsScreenSnapshot, now: Long) {
        if (!KIDS_TAP_TO_REVEAL_CONTROLS || currentVideo != null || overlay.current != null ||
            snapshot.loadingSpinnerPresent || snapshot.title != null || snapshot.channel != null ||
            snapshot.controlsVisible || kidsPlayerSince == 0L || now < kidsNextRevealTime || kidsRevealCount >= 4
        ) return

        kidsRevealCount++
        kidsNextRevealTime = now + 3000L

        val watchPlayerNode = findKidsNodeBySuffix(root, "watch_player")
            ?: findKidsNodeBySuffix(root, "watch_player_container")

        var dispatched = false
        if (watchPlayerNode != null) {
            val bounds = Rect()
            watchPlayerNode.getBoundsInScreen(bounds)
            val cx = bounds.centerX()
            val cy = bounds.centerY()

            if (cx > 0 && cy > 0) {
                try {
                    val path = Path().apply { moveTo(cx.toFloat(), cy.toFloat()) }
                    val stroke = GestureDescription.StrokeDescription(path, 0L, 50L)
                    val gesture = GestureDescription.Builder().addStroke(stroke).build()

                    dispatched = dispatchGesture(gesture, object : GestureResultCallback() {
                        override fun onCompleted(gestureDescription: GestureDescription?) {
                            Log.i(TAG, "KIDS tap gesture dispatched=true completed")
                        }

                        override fun onCancelled(gestureDescription: GestureDescription?) {
                            Log.i(TAG, "KIDS tap gesture dispatched=true cancelled")
                        }
                    }, mainHandler)

                    if (dispatched) {
                        mainHandler.postDelayed({
                            val titleVisible = (currentVideo != null || kidsLastObservedTitle != null)
                            Log.i(TAG, "KIDS tap result title_visible=$titleVisible")
                        }, 1000)
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "Failed dispatchGesture: $e")
                }
            }
        }

        if (!dispatched) {
            Log.i(TAG, "KIDS tap gesture dispatched=false")
            val clicked = watchPlayerNode?.let { clickNodeOrClickableParent(it) } ?: false
            Log.i(TAG, "KIDS tap_to_reveal fallback result=${if (clicked) "clicked" else "not_clickable"}")
        }
    }

    private fun findKidsNodeBySuffix(node: AccessibilityNodeInfo, suffix: String): AccessibilityNodeInfo? {
        val shortId = node.viewIdResourceName?.substringAfter(":id/")
        if (shortId?.endsWith(suffix) == true) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findKidsNodeBySuffix(child, suffix)
            if (found != null) return found
        }
        return null
    }

    private fun clickNodeOrClickableParent(node: AccessibilityNodeInfo): Boolean {
        var target: AccessibilityNodeInfo? = node
        while (target != null && !target.isClickable) target = target.parent
        return target?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true
    }

    private fun processWindow(now: Long) {
        val root = rootInActiveWindow ?: return
        if (root.packageName?.toString() != YOUTUBE) return

        val sb = StringBuilder()
        val ids = mutableSetOf<String>()
        val info = VideoInfo()
        walk(root, 0, sb, ids, info, false)

        val screen = when {
            info.shorts -> "SHORTS"
            info.title != null -> "WATCH"
            info.markers.any { it.startsWith("modern_miniplayer") || it.startsWith("miniplayer_") } -> "MINIPLAYER"
            "watch_player" in info.markers -> "PLAYER_NO_TITLE"
            else -> "OTHER"
        }

        lastScreen = screen
        updateCurrentVideo(screen, info, now)

        if (screen == "OTHER") {
            if (otherScreenSince == 0L) {
                otherScreenSince = now
            } else if (currentVideo != null && now - otherScreenSince >= 1000) {
                Log.i(TAG, "RESET (no player on screen)")
                currentVideo = null
                otherScreenSince = 0L
                refreshOverlay()
            }
        } else {
            otherScreenSince = 0L
        }

        maybeSend(now)
        refreshOverlay()

        val cur = currentVideo
        val lines = mutableListOf<String>()
        lines.add("screen: $screen")
        lines.add("current: ${cur?.title ?: "none"} (${cur?.channel ?: "?"}, ${cur?.durationSec ?: "?"}s)")
        Probe.videoIds = ids
        Probe.candidates = lines
        Probe.dump = sb.toString().take(30000)
        try {
            java.io.File(filesDir, "dump.txt").writeText(Probe.dump)
        } catch (_: Exception) {}

        // Log only when the result changes, to keep Logcat readable.
        val summary = "screen=$screen | title=${info.title} | handle=${info.handle} | " +
            "channel=${info.channelName} | duration=${info.durationSec} | " +
            "CURRENT=${cur?.title?.take(30)} status=${cur?.status} | markers=${info.markers}"
        if (summary != lastLogged) {
            lastLogged = summary
            Log.i(TAG, summary)
        }
        if (info.shorts) logShorts(info)
    }

    private fun updateCurrentVideo(screen: String, info: VideoInfo, now: Long) {
        when (screen) {
            "SHORTS" -> {
                if (currentVideo != null) {
                    Log.i(TAG, "RESET (entered Shorts)")
                    currentVideo = null
                }
            }
            "WATCH" -> {
                val title = info.title ?: return
                var v = currentVideo
                if (v == null || v.title != title) {
                    Log.i(TAG, "NEW VIDEO title=$title")
                    v = CurrentVideo(title, now)
                    decisionCache[title]?.let { cachedStatus ->
                        v.status = cachedStatus
                    }
                    currentVideo = v
                    startTickerIfNeeded()
                }
                info.handle?.let { v.handle = it }
                info.channelName?.let { v.channel = it }
                // The duration is only readable while the controls are visible, and a
                // wrong small value (like 0:15) has been seen, so keep the largest.
                info.durationSec?.let { d ->
                    if (d > (v.durationSec ?: 0)) v.durationSec = d
                }
            }
            else -> {
                // Fullscreen, miniplayer, endscreen, feed: keep the current video as is.
            }
        }
    }

    private fun maybeSend(now: Long) {
        val v = currentVideo ?: return
        val channel = v.channel ?: return
        if (v.sent || v.attempts >= 3) return
        if (now - v.firstSeen < ProbeConfig.SETTLE_MS || now < v.retryAt) return

        val profileId = ProfileManager.getProfileId(this)
        if (profileId <= 0) {
            Log.i(TAG, "NO PROFILE SET -> cannot identify")
            v.status = "ask_parent"
            refreshOverlay()
            return
        }

        v.sent = true
        v.attempts++
        val title = v.title
        val duration = v.durationSec
        worker.execute {
            var ok = false
            try {
                Log.i(TAG, "SEND title=$title channel=$channel duration=$duration profile_id=$profileId")
                val res = backend.identify(title, channel, duration, profileId)
                Log.i(TAG, "BACKEND http=${res.http} title=$title body=${res.body.take(500)}")
                if (res.http == 200) {
                    val json = JSONObject(res.body)
                    val status = if (json.optBoolean("identified")) {
                        val c = json.getJSONObject("classification")
                        val st = c.getString("status")
                        Log.i(
                            TAG,
                            "DECISION status=$st confidence=${c.optDouble("confidence")} " +
                                "match=${json.optString("match_confidence")} title=$title"
                        )
                        st
                    } else {
                        Log.i(TAG, "DECISION unidentified (treat as ask_parent) title=$title")
                        "ask_parent"
                    }
                    ok = true

                    mainHandler.post {
                        val cur = currentVideo
                        if (cur === v) {
                            v.status = status
                            decisionCache[title] = status
                            refreshOverlay()
                        } else if (cur != null && cur.title == v.title && (cur.channel == v.channel || cur.channel == null || v.channel == null)) {
                            Log.i(TAG, "DECISION applied late title=$title")
                            v.status = status
                            cur.status = status
                            decisionCache[title] = status
                            refreshOverlay()
                        } else {
                            Log.i(TAG, "DECISION ignored (different video) title=$title")
                        }
                        if (!shouldRunTicker()) stopTicker()
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "BACKEND error title=$title: $e")
            }

            if (!ok) {
                mainHandler.post {
                    v.retryAt = System.currentTimeMillis() + 10_000
                    v.sent = false
                    if (v.attempts >= 3) {
                        if (currentVideo === v) {
                            v.status = "ask_parent"
                            refreshOverlay()
                        }
                    }
                    if (!shouldRunTicker()) stopTicker()
                }
            }
        }
    }

    private fun refreshOverlay() {
        if (!OVERLAY_ENABLED) return

        val now = System.currentTimeMillis()
        val isKids = KIDS_ENFORCEMENT_ENABLED && currentAppPkg == YOUTUBE_KIDS
        val profileSet = ProfileManager.isProfileSet(this)

        val rawKidsKind: OverlayKind? = if (!profileSet) {
            if (lastScreen == "KIDS_PLAYER" || lastScreen == "WATCH" || lastScreen == "PLAYER_NO_TITLE" || lastScreen == "MINIPLAYER") {
                OverlayKind.SETUP
            } else null
        } else if (isKids) {
            when {
                lastScreen != "KIDS_PLAYER" -> null
                now < kidsGoBackUntil -> null
                currentVideo == null -> {
                    if (kidsTitleWaitSince == 0L) kidsTitleWaitSince = now
                    if (now - kidsTitleWaitSince >= KIDS_TITLE_WAIT_MS) {
                        if (!kidsTitleWaitLogged) {
                            kidsTitleWaitLogged = true
                            Log.i(TAG, "KIDS title wait expired")
                        }
                        OverlayKind.ASK
                    } else {
                        OverlayKind.CHECKING
                    }
                }
                else -> when (currentVideo?.status) {
                    null -> OverlayKind.CHECKING
                    "block" -> OverlayKind.BLOCK
                    "ask_parent" -> OverlayKind.ASK
                    "allow" -> null
                    else -> OverlayKind.CHECKING
                }
            }
        } else null

        val targetKind: OverlayKind? = if (!profileSet) {
            rawKidsKind
        } else if (isKids) {
            if (rawKidsKind == OverlayKind.CHECKING && !KIDS_SHOW_CHECKING_OVERLAY) {
                null
            } else {
                rawKidsKind
            }
        } else when {
            SHORTS_BLOCKED && lastScreen == "SHORTS" -> {
                unknownPlayerSince = 0L
                OverlayKind.SHORTS
            }
            lastScreen == "SHORTS" || lastScreen == "OTHER" -> {
                unknownPlayerSince = 0L
                null
            }
            (lastScreen == "WATCH" || lastScreen == "PLAYER_NO_TITLE" || lastScreen == "MINIPLAYER") && currentVideo == null -> {
                if (lastScreen == "WATCH") {
                    unknownPlayerSince = 0L
                    // TODO: UNKNOWN overlay for player-without-title, after feed tests
                    null
                } else {
                    if (unknownPlayerSince == 0L) unknownPlayerSince = now
                    if (now - unknownPlayerSince > 6000) {
                        if (overlay.current != OverlayKind.ASK) {
                            Log.i(TAG, "UNKNOWN player -> ASK")
                        }
                        OverlayKind.ASK
                    } else {
                        OverlayKind.CHECKING
                    }
                }
            }
            lastScreen == "WATCH" || lastScreen == "PLAYER_NO_TITLE" || lastScreen == "MINIPLAYER" -> {
                unknownPlayerSince = 0L
                when (currentVideo?.status) {
                    null -> OverlayKind.CHECKING
                    "block" -> OverlayKind.BLOCK
                    "ask_parent" -> OverlayKind.ASK
                    "allow" -> null
                    else -> null
                }
            }
            else -> {
                unknownPlayerSince = 0L
                null
            }
        }

        if (isKids && profileSet) {
            val state = when (rawKidsKind) {
                OverlayKind.CHECKING -> {
                    val reason = if (currentVideo == null) "waiting_for_title" else "backend_pending"
                    val silentSuffix = if (!KIDS_SHOW_CHECKING_OVERLAY) " silent=true" else ""
                    "Checking$silentSuffix" to reason
                }
                OverlayKind.BLOCK -> "Blocked" to "backend_block"
                OverlayKind.ASK -> {
                    val reason = if (currentVideo == null) "title_wait_expired" else "backend_ask_parent"
                    "Ask" to reason
                }
                OverlayKind.SHORTS -> null
                OverlayKind.SETUP -> null
                null -> if (currentVideo?.status == "allow") "Allow" to "backend_allow" else null
            }
            if (state == null) {
                kidsLastState = ""
            } else {
                val key = "${state.first}:${state.second}"
                if (key != kidsLastState) {
                    kidsLastState = key
                    Log.i(TAG, "KIDS state ${state.first} reason=${state.second}")
                }
            }
        }

        val currentKind = overlay.current

        if (targetKind != currentKind) {
            if (targetKind != null) {
                val wasShowing = currentKind != null
                overlay.show(targetKind)
                if (!wasShowing && overlay.current != null) {
                    silencePlayback()
                    if (isKids && KIDS_AUTO_CLOSE_ON_BLOCK && (targetKind == OverlayKind.BLOCK || targetKind == OverlayKind.ASK)) {
                        mainHandler.postDelayed({
                            if (overlay.current == OverlayKind.BLOCK || overlay.current == OverlayKind.ASK) {
                                Log.i(TAG, "KIDS auto-close fired")
                                performKidsGoBack()
                            }
                        }, 3000)
                    }
                }
                if (overlay.current != null) {
                    startWatchdogIfNeeded()
                    startTickerIfNeeded()
                }
            } else {
                val isAllow = currentVideo?.status == "allow"
                val reason = if (isAllow) "allow" else if (currentVideo == null) "no_video" else "screen_$lastScreen"
                overlay.hide(reason)
                restorePlayback(isAllow)
                stopWatchdog()
                if (!shouldRunTicker()) {
                    stopTicker()
                }
            }
        }
    }

    private fun findPauseButton(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val desc = node.contentDescription?.toString()
        val shortId = node.viewIdResourceName?.substringAfter(":id/")

        val isPauseDesc = desc != null && desc.startsWith("Pause", ignoreCase = true)
        val isPlayPauseId = shortId != null && shortId.contains("play_pause") &&
            (desc == null || !desc.startsWith("Play", ignoreCase = true))

        if (isPauseDesc || isPlayPauseId) {
            return node
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findPauseButton(child)
            if (found != null) return found
        }
        return null
    }

    private fun findKidsPauseNode(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val desc = node.contentDescription?.toString()
        val shortId = node.viewIdResourceName?.substringAfter(":id/")

        val isPauseId = shortId != null && shortId.endsWith("pause")
        val isPauseDesc = desc != null && (desc.equals("Pause video", ignoreCase = true) || desc.startsWith("Pause", ignoreCase = true))

        if (isPauseId || isPauseDesc) {
            return node
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findKidsPauseNode(child)
            if (found != null) return found
        }
        return null
    }

    private fun findPlayButton(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val desc = node.contentDescription?.toString()
        if (desc != null && desc.startsWith("Play video", ignoreCase = true)) {
            return node
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findPlayButton(child)
            if (found != null) return found
        }
        return null
    }

    private fun silencePlayback() {
        var focusOk = false
        try {
            val am = getSystemService(AUDIO_SERVICE) as AudioManager
            val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE)
                        .build()
                )
                .setOnAudioFocusChangeListener { /* no-op */ }
                .build()
            audioFocusRequest = req
            val res = am.requestAudioFocus(req)
            focusOk = (res == AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
        } catch (e: Exception) {
            Log.w(TAG, "Failed audio focus: $e")
        }

        val am = getSystemService(AUDIO_SERVICE) as AudioManager
        val volBefore = try { am.getStreamVolume(AudioManager.STREAM_MUSIC) } catch (_: Exception) { 0 }
        if (savedMusicVolume == null) {
            savedMusicVolume = volBefore
        }

        var errMute = "none"
        try {
            am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, 0)
        } catch (e: Exception) {
            errMute = e.javaClass.simpleName
        }
        val volMute = try { am.getStreamVolume(AudioManager.STREAM_MUSIC) } catch (_: Exception) { -1 }
        val isMuted1 = try { am.isStreamMute(AudioManager.STREAM_MUSIC) } catch (_: Exception) { false }
        Log.i(TAG, "SILENCE step=mute vol=$volMute muted=$isMuted1 err=$errMute")

        var errSet0 = "none"
        try {
            am.setStreamVolume(AudioManager.STREAM_MUSIC, 0, 0)
        } catch (e: Exception) {
            errSet0 = e.javaClass.simpleName
        }
        var volSet0 = try { am.getStreamVolume(AudioManager.STREAM_MUSIC) } catch (_: Exception) { -1 }
        val isMuted2 = try { am.isStreamMute(AudioManager.STREAM_MUSIC) } catch (_: Exception) { false }
        Log.i(TAG, "SILENCE step=set0 vol=$volSet0 muted=$isMuted2 err=$errSet0")

        if (volSet0 > 0) {
            var errLower = "none"
            var loopCount = 0
            var prevVol = volSet0
            try {
                while (loopCount < 20) {
                    am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, 0)
                    loopCount++
                    val curVol = am.getStreamVolume(AudioManager.STREAM_MUSIC)
                    if (curVol <= 0 || curVol == prevVol) {
                        volSet0 = curVol
                        break
                    }
                    prevVol = curVol
                }
            } catch (e: Exception) {
                errLower = e.javaClass.simpleName
            }
            val isMuted3 = try { am.isStreamMute(AudioManager.STREAM_MUSIC) } catch (_: Exception) { false }
            Log.i(TAG, "SILENCE step=loop_lower vol=$volSet0 muted=$isMuted3 err=$errLower")
        }

        pausedByUs = true

        val focusStr = if (focusOk) "ok" else "fail"
        Log.i(TAG, "SILENCE focus=$focusStr mute=ok click=none")

        try {
            am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_MEDIA_PAUSE))
            am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_MEDIA_PAUSE))
        } catch (e: Exception) {
            Log.w(TAG, "Failed media key pause: $e")
        }

        attemptKidsPauseClick(attempt = 1)
    }

    private fun attemptKidsPauseClick(attempt: Int) {
        val root = rootInActiveWindow
        var pauseClicked = false
        if (root != null) {
            val pauseNode = findPauseButton(root) ?: findKidsPauseNode(root)
            if (pauseNode != null) {
                pauseClicked = clickNodeOrClickableParent(pauseNode)
            }
        }

        if (pauseClicked) {
            Log.i(TAG, "SILENCE pause_click=ok attempt=$attempt")
            return
        }

        if (attempt == 1) {
            if (root != null) {
                val watchPlayerNode = findKidsNodeBySuffix(root, "watch_player")
                    ?: findKidsNodeBySuffix(root, "watch_player_container")
                if (watchPlayerNode != null) {
                    val tapped = dispatchTapOnNode(watchPlayerNode)
                    if (!tapped) {
                        clickNodeOrClickableParent(watchPlayerNode)
                    }
                }
            }
            mainHandler.postDelayed({
                attemptKidsPauseClick(2)
            }, 400)
        } else if (attempt == 2) {
            mainHandler.postDelayed({
                attemptKidsPauseClick(3)
            }, 300)
        } else {
            Log.i(TAG, "SILENCE pause_click=none attempt=3")
        }
    }

    private fun dispatchTapOnNode(node: AccessibilityNodeInfo): Boolean {
        val bounds = Rect()
        node.getBoundsInScreen(bounds)
        val cx = bounds.centerX()
        val cy = bounds.centerY()
        if (cx <= 0 || cy <= 0) return false
        return try {
            val path = Path().apply { moveTo(cx.toFloat(), cy.toFloat()) }
            val stroke = GestureDescription.StrokeDescription(path, 0L, 50L)
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            dispatchGesture(gesture, null, mainHandler)
        } catch (_: Exception) {
            false
        }
    }

    private fun restorePlayback(resume: Boolean) {
        val am = getSystemService(AUDIO_SERVICE) as AudioManager
        if (pausedByUs || savedMusicVolume != null) {
            try {
                am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_UNMUTE, 0)
            } catch (e: Exception) {
                Log.w(TAG, "Failed unmute: $e")
            }
            savedMusicVolume?.let { savedVol ->
                try {
                    am.setStreamVolume(AudioManager.STREAM_MUSIC, savedVol, 0)
                } catch (e: Exception) {
                    Log.w(TAG, "Failed restore setStreamVolume: $e")
                }
                savedMusicVolume = null
            }
            audioFocusRequest?.let { req ->
                try {
                    am.abandonAudioFocusRequest(req)
                } catch (e: Exception) {
                    Log.w(TAG, "Failed abandon focus: $e")
                }
                audioFocusRequest = null
            }
            pausedByUs = false
        }

        if (resume) {
            try {
                am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_MEDIA_PLAY))
                am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_MEDIA_PLAY))
            } catch (e: Exception) {
                Log.w(TAG, "Failed media key play: $e")
            }

            try {
                val root = rootInActiveWindow
                if (root != null) {
                    val playNode = findPlayButton(root)
                    if (playNode != null) {
                        clickNodeOrClickableParent(playNode)
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed play click: $e")
            }
        }
    }

    private fun performGoBack(attempt: Int) {
        if (overlay.current == null) return

        if (currentAppPkg == YOUTUBE_KIDS && KIDS_ENFORCEMENT_ENABLED) {
            performKidsGoBack()
            return
        }

        if (overlay.current == OverlayKind.SHORTS) {
            performGlobalAction(GLOBAL_ACTION_BACK)
            Log.i(TAG, "GOBACK attempt 1 action=back")
            mainHandler.postDelayed({
                if (overlay.current == OverlayKind.SHORTS || lastScreen == "SHORTS") {
                    performGlobalAction(GLOBAL_ACTION_HOME)
                    Log.i(TAG, "GOBACK attempt 2 action=home")
                }
            }, 800)
            return
        }

        val root = rootInActiveWindow
        var closeClicked = false
        if (root != null && root.packageName?.toString() == YOUTUBE) {
            val closeNodes = root.findAccessibilityNodeInfosByViewId("com.google.android.youtube:id/modern_miniplayer_close")
            if (!closeNodes.isNullOrEmpty()) {
                for (node in closeNodes) {
                    if (!closeClicked) {
                        var target: AccessibilityNodeInfo? = node
                        while (target != null && !target.isClickable) {
                            target = target.parent
                        }
                        if (target != null && target.isClickable) {
                            closeClicked = target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                        }
                    }
                }
            }
        }

        if (closeClicked) {
            Log.i(TAG, "GOBACK attempt $attempt action=close_miniplayer")
            currentVideo = null
            refreshOverlay()
            return
        }

        performGlobalAction(GLOBAL_ACTION_BACK)
        Log.i(TAG, "GOBACK attempt $attempt action=back")

        if (attempt < 4) {
            mainHandler.postDelayed({ performGoBack(attempt + 1) }, 800)
        } else {
            performGlobalAction(GLOBAL_ACTION_HOME)
            Log.i(TAG, "GOBACK attempt 4 action=home")
        }
    }

    private fun performKidsGoBack() {
        val now = System.currentTimeMillis()
        kidsGoBackUntil = now + 2000L

        resetKidsTracking("go_back")

        val root = rootInActiveWindow
        var clicked = false
        if (root?.packageName?.toString() == YOUTUBE_KIDS) {
            findKidsNodeBySuffix(root, "back_button")?.let { clicked = clickNodeOrClickableParent(it) }
        }
        val action = if (clicked) "back_button" else "back"
        if (!clicked) performGlobalAction(GLOBAL_ACTION_BACK)
        Log.i(TAG, "KIDS GOBACK step=1 action=$action")

        lastScreen = "KIDS_PLAYER"
        overlay.hide("kids_go_back")
        restorePlayback(false)
        stopWatchdog()
        startTickerIfNeeded()

        // Step 2: after 1 second, check if watch_player is still on screen
        mainHandler.postDelayed({
            val root2 = rootInActiveWindow
            val stillPlaying2 = root2 != null &&
                root2.packageName?.toString() == YOUTUBE_KIDS &&
                findKidsNodeBySuffix(root2, "watch_player") != null

            if (stillPlaying2) {
                performGlobalAction(GLOBAL_ACTION_BACK)
                Log.i(TAG, "KIDS GOBACK step=2 action=back")

                // Step 3: after another 1 second, check if watch_player is still on screen
                mainHandler.postDelayed({
                    val root3 = rootInActiveWindow
                    val stillPlaying3 = root3 != null &&
                        root3.packageName?.toString() == YOUTUBE_KIDS &&
                        findKidsNodeBySuffix(root3, "watch_player") != null

                    if (stillPlaying3) {
                        performGlobalAction(GLOBAL_ACTION_HOME)
                        Log.i(TAG, "KIDS GOBACK step=3 action=home")
                    } else {
                        Log.i(TAG, "KIDS GOBACK player gone")
                    }
                    if (!shouldRunTicker()) stopTicker()
                }, 1000)
            } else {
                Log.i(TAG, "KIDS GOBACK player gone")
                if (!shouldRunTicker()) stopTicker()
            }
        }, 1000)
    }

    private fun startWatchdogIfNeeded() {
        if (!watchdogRunning && overlay.current != null) {
            watchdogRunning = true
            rootNullSince = 0L
            mainHandler.postDelayed(watchdogRunnable, 1000)
        }
    }

    private fun stopWatchdog() {
        if (watchdogRunning) {
            mainHandler.removeCallbacks(watchdogRunnable)
            watchdogRunning = false
            rootNullSince = 0L
        }
    }

    private fun logShorts(info: VideoInfo) {
        val h = info.textNodes.hashCode()
        if (h == lastShortsHash) return
        lastShortsHash = h
        Log.i(TAG, "SHORTS screen: ${info.textNodes.size} text nodes")
        if (DUMP_SHORTS_NODES) {
            info.textNodes.forEach { Log.i(TAG, "SHORTS node $it") }
        }
    }

    private fun isTransient(pkg: String) =
        pkg == "android" || pkg == "com.android.systemui" ||
            pkg == "com.vivo.card" || pkg == packageName ||
            pkg.contains("inputmethod") || pkg.contains(".ime")

    private fun parseClock(s: String): Int? {
        val parts = s.trim().split(":")
        if (parts.size !in 2..3) return null
        var total = 0
        for (p in parts) {
            if (p.isEmpty() || p.length > 3 || !p.all { it.isDigit() }) return null
            total = total * 60 + p.toInt()
        }
        return total
    }

    private fun walk(
        node: AccessibilityNodeInfo,
        depth: Int,
        sb: StringBuilder,
        ids: MutableSet<String>,
        info: VideoInfo,
        inWatchList: Boolean,
    ) {
        if (depth > 40) return
        val text = node.text?.toString()
        val desc = node.contentDescription?.toString()
        val shortId = node.viewIdResourceName?.substringAfter(":id/")
        val inList = inWatchList || shortId == "watch_list"
        if (shortId != null && markerWords.any { shortId.contains(it) }) info.markers.add(shortId)
        if (shortId == "reel_recycler" || shortId == "reel_watch_fragment_root") info.shorts = true
        if (desc != null && desc.contains("fullscreen", ignoreCase = true)) {
            info.markers.add("desc:" + desc.take(25))
        }

        if (!text.isNullOrBlank() || !desc.isNullOrBlank() || shortId != null) {
            sb.append("  ".repeat(depth))
                .append(node.className?.toString()?.substringAfterLast('.'))
                .append(" id=").append(shortId)
                .append(" text=").append(text)
                .append(" desc=").append(desc)
                .append('\n')
        }
        if ((!text.isNullOrBlank() || !desc.isNullOrBlank()) && info.textNodes.size < 150) {
            info.textNodes.add("id=$shortId text=${text?.take(120)} desc=${desc?.take(120)}")
        }

        listOfNotNull(text, desc).forEach { s ->
            urlRegex.findAll(s).forEach { ids.add(it.groupValues[1]) }
        }

        // Title: first valid text node inside the watch_list that has no view id.
        if (inList && info.title == null && shortId == null && !text.isNullOrBlank()) {
            val trimmed = text.trim()
            if (isValidTitle(trimmed)) {
                info.title = trimmed
            }
        }
        // Channel handle, e.g. "@MrBeast"
        if (inList && info.handle == null && text != null && text.trim().startsWith("@")) {
            info.handle = text.trim()
        }
        // Channel display name from "<name> 520M subscribers"
        if (inList && info.channelName == null && desc != null) {
            subsRegex.find(desc.trim())?.let { info.channelName = it.groupValues[1] }
        }
        if (shortId == "time_bar_total_time" && !text.isNullOrBlank()) {
            parseClock(text.replace("/", "").trim())?.let { info.durationSec = it }
        }
        if (shortId == "time_bar_current_time" && !text.isNullOrBlank()) {
            parseClock(text.trim())?.let { info.currentSec = it }
        }

        for (i in 0 until node.childCount) {
            node.getChild(i)?.let { walk(it, depth + 1, sb, ids, info, inList) }
        }
    }

    override fun onInterrupt() {}

    override fun onUnbind(intent: Intent?): Boolean {
        mainHandler.removeCallbacksAndMessages(null)
        watchdogRunning = false
        tickerRunning = false
        if (overlay.current != null) {
            overlay.hide("unbind")
        }
        restorePlayback(false)
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        mainHandler.removeCallbacksAndMessages(null)
        watchdogRunning = false
        tickerRunning = false
        if (overlay.current != null) {
            overlay.hide("destroy")
        }
        restorePlayback(false)
        worker.shutdownNow()
        super.onDestroy()
    }

    companion object {
        const val YOUTUBE = "com.google.android.youtube"
        const val YOUTUBE_KIDS = "com.google.android.apps.youtube.kids"
        val SUPPORTED_PACKAGES = setOf(YOUTUBE, YOUTUBE_KIDS)

        const val TAG = "KidsCrown"
        const val OVERLAY_ENABLED = true
        const val DUMP_SHORTS_NODES = false
        const val SHORTS_BLOCKED = true
        const val KIDS_ENFORCEMENT_ENABLED = true
        const val KIDS_SHOW_CHECKING_OVERLAY = false
        const val DUMP_KIDS_NODES = false
        const val KIDS_TITLE_WAIT_MS = 15_000L
        const val KIDS_TAP_TO_REVEAL_CONTROLS = true
        const val KIDS_REVEAL_DELAY_MS = 1_500L
        const val KIDS_AUTO_CLOSE_ON_BLOCK = true
    }
}
