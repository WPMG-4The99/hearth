package io.github.nanomuse.panic

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.openminis.app.R
import io.github.nanomuse.hub.IncomingCall
import org.json.JSONObject

/**
 * A household panic on this phone. It does not require Remote control: the only thing another
 * account may ask for is this alert. Delivery is the notification (and the screen, when the
 * system allows a full-screen intent).
 */
object PanicAlert {
    private const val CHANNEL = "hearth_panic"
    private const val NOTIFY_ID = 0x4845

    fun show(context: Context, call: IncomingCall) {
        val who = call.from.optString("who").ifBlank { call.senderName }
        val title = context.getString(if (call.action == "alert") R.string.nm_panic_alert_title else R.string.nm_panic_alert_title)
        val body = context.getString(R.string.nm_panic_from, who)
        val shown = runCatching { post(context, title, body, who) }.isSuccess
        if (shown) call.result(JSONObject().put("delivered", true))
        else call.fail("not_delivered", "this phone could not show the alert")
    }

    private fun post(context: Context, title: String, body: String, who: String) {
        val app = context.applicationContext
        val nm = app.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(CHANNEL, app.getString(R.string.nm_panic_channel), NotificationManager.IMPORTANCE_HIGH).apply {
                description = app.getString(R.string.nm_panic_channel_sub)
                setBypassDnd(false)
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 400, 200, 400, 200, 800)
                setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM), android.media.AudioAttributes.Builder()
                    .setUsage(android.media.AudioAttributes.USAGE_ALARM)
                    .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build())
            }
            nm.createNotificationChannel(channel)
        }
        val open = Intent(app, PanicAlertActivity::class.java)
            .putExtra(PanicAlertActivity.EXTRA_WHO, who)
            .putExtra(PanicAlertActivity.EXTRA_BODY, body)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val pending = PendingIntent.getActivity(app, 0, open, flags)
        val notification = NotificationCompat.Builder(app, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_nanomuse)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM))
            .setVibrate(longArrayOf(0, 400, 200, 400, 200, 800))
            .setFullScreenIntent(pending, true)
            .setContentIntent(pending)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(app).notify(NOTIFY_ID, notification)
        runCatching { app.startActivity(open) }
    }
}

/** The screen the panic notification opens: who sent it, and a way to dismiss it. */
class PanicAlertActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            )
        }
        val who = intent.getStringExtra(EXTRA_WHO).orEmpty()
        val body = intent.getStringExtra(EXTRA_BODY) ?: getString(R.string.nm_panic_from, who)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(0xFF8C1D18.toInt())
            setPadding(48, 48, 48, 48)
        }
        root.addView(TextView(this).apply {
            text = getString(R.string.nm_panic_alert_title)
            setTextColor(0xFFFFFFFF.toInt())
            textSize = 28f
            gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = body
            setTextColor(0xFFFFFFFF.toInt())
            textSize = 22f
            gravity = Gravity.CENTER
            setPadding(0, 32, 0, 48)
        })
        root.addView(Button(this).apply {
            text = getString(R.string.nm_panic_dismiss)
            setOnClickListener {
                NotificationManagerCompat.from(this@PanicAlertActivity).cancel(0x4845)
                finish()
            }
        })
        setContentView(root)
        runCatching {
            val ring = RingtoneManager.getRingtone(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM))
            ring?.play()
        }
    }

    companion object {
        const val EXTRA_WHO = "who"
        const val EXTRA_BODY = "body"
    }
}
