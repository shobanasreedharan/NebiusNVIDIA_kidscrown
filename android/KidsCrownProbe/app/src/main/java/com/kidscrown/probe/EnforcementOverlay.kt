package com.kidscrown.probe

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.util.Log
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

enum class OverlayKind(val titleText: String, val messageText: String) {
    CHECKING("Checking this video…", "KidsCrown is checking if it's OK to watch."),
    BLOCK("This video is blocked", "Your parent has said no to this one."),
    ASK("Ask a parent first", "A parent needs to say yes before you can watch this."),
    SHORTS("Shorts are off", "Ask a parent if you want to watch Shorts."),
    SETUP("Set up KidsCrown", "Open the KidsCrown app on this phone and choose who uses it.")
}

class EnforcementOverlay(
    private val context: Context,
    private val onGoBack: () -> Unit
) {
    private val windowManager by lazy {
        context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    }

    private val density = context.resources.displayMetrics.density
    private fun dp(dp: Int): Int = (dp * density).toInt()

    var current: OverlayKind? = null
        private set

    private var isAdded = false

    private val titleView = TextView(context).apply {
        textSize = 22f
        setTypeface(null, Typeface.BOLD)
        setTextColor(Color.WHITE)
        gravity = Gravity.CENTER
        setPadding(0, 0, 0, dp(24))
    }

    private val messageView = TextView(context).apply {
        textSize = 16f
        setTextColor(Color.parseColor("#CCCCCC"))
        gravity = Gravity.CENTER
        setPadding(0, 0, 0, dp(48))
    }

    private val goBackButton = Button(context).apply {
        text = "Go back"
        textSize = 16f
        setOnClickListener { onGoBack() }
    }

    private val rootView = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setBackgroundColor(Color.parseColor("#121212"))
        val p48 = dp(48)
        setPadding(p48, p48, p48, p48)
        isClickable = true
        isFocusable = false

        addView(titleView)
        addView(messageView)
        addView(goBackButton)
    }

    private val layoutParams = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT
    )

    fun show(kind: OverlayKind) {
        titleView.text = kind.titleText
        messageView.text = kind.messageText

        if (!isAdded) {
            try {
                windowManager.addView(rootView, layoutParams)
                isAdded = true
            } catch (e: Exception) {
                Log.e(KidsCrownService.TAG, "Failed to add overlay window: $e")
                return
            }
        }
        current = kind
        Log.i(KidsCrownService.TAG, "OVERLAY show kind=$kind")
    }

    fun hide(reason: String = "normal") {
        if (isAdded) {
            try {
                windowManager.removeView(rootView)
            } catch (e: Exception) {
                Log.e(KidsCrownService.TAG, "Failed to remove overlay window: $e")
            }
            isAdded = false
        }
        current = null
        Log.i(KidsCrownService.TAG, "OVERLAY hide reason=$reason")
    }
}
