package app.thirtysixmonths.engine;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import java.util.Calendar;

/**
 * Alarm plumbing for the two recurring nudges.
 *
 * Each alarm schedules only its next occurrence and re-arms itself when it
 * fires. setAndAllowWhileIdle gets through Doze without asking for the exact
 * alarm permission, which a study reminder does not need — a few minutes of
 * drift is fine, a reminder that never arrives is not.
 */
final class Reminders {

    static final String PREFS = "reminders";
    static final String ACTION_DAILY = "app.thirtysixmonths.engine.DAILY";
    static final String ACTION_WATER = "app.thirtysixmonths.engine.WATER";

    private static final int RC_DAILY = 1001;
    private static final int RC_WATER = 1002;

    private Reminders() {}

    static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static PendingIntent intent(Context ctx, String action, int rc) {
        Intent i = new Intent(ctx, ReminderReceiver.class).setAction(action);
        return PendingIntent.getBroadcast(ctx, rc, i,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private static void armAt(Context ctx, long at, PendingIntent pi) {
        AlarmManager am = ctx.getSystemService(AlarmManager.class);
        if (am == null) return;
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
    }

    // ---- daily study reminder -------------------------------------------

    static void setDaily(Context ctx, boolean on, int hour, int minute) {
        prefs(ctx).edit()
                .putBoolean("daily_on", on)
                .putInt("daily_h", hour)
                .putInt("daily_m", minute)
                .apply();
        if (on) scheduleDaily(ctx); else cancelDaily(ctx);
    }

    static void scheduleDaily(Context ctx) {
        SharedPreferences p = prefs(ctx);
        if (!p.getBoolean("daily_on", false)) return;

        Calendar c = Calendar.getInstance();
        c.set(Calendar.HOUR_OF_DAY, p.getInt("daily_h", 19));
        c.set(Calendar.MINUTE, p.getInt("daily_m", 15));
        c.set(Calendar.SECOND, 0);
        c.set(Calendar.MILLISECOND, 0);
        if (c.getTimeInMillis() <= System.currentTimeMillis()) {
            c.add(Calendar.DAY_OF_YEAR, 1);
        }
        armAt(ctx, c.getTimeInMillis(), intent(ctx, ACTION_DAILY, RC_DAILY));
    }

    static void cancelDaily(Context ctx) {
        AlarmManager am = ctx.getSystemService(AlarmManager.class);
        if (am != null) am.cancel(intent(ctx, ACTION_DAILY, RC_DAILY));
    }

    // ---- hourly water nudge ---------------------------------------------

    static void setWater(Context ctx, boolean on, int startMin, int endMin) {
        prefs(ctx).edit()
                .putBoolean("water_on", on)
                .putInt("water_start", startMin)
                .putInt("water_end", endMin)
                .apply();
        if (on) scheduleWater(ctx); else cancelWater(ctx);
    }

    static void scheduleWater(Context ctx) {
        if (!prefs(ctx).getBoolean("water_on", false)) return;
        armAt(ctx, System.currentTimeMillis() + AlarmManager.INTERVAL_HOUR,
                intent(ctx, ACTION_WATER, RC_WATER));
    }

    static void cancelWater(Context ctx) {
        AlarmManager am = ctx.getSystemService(AlarmManager.class);
        if (am != null) am.cancel(intent(ctx, ACTION_WATER, RC_WATER));
    }

    /** True when the clock is inside the waking window the app was told about. */
    static boolean waterWindowOpen(Context ctx) {
        SharedPreferences p = prefs(ctx);
        int start = p.getInt("water_start", 5 * 60 + 15);
        int end = p.getInt("water_end", 23 * 60);
        Calendar c = Calendar.getInstance();
        int now = c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE);
        return start <= end ? (now >= start && now <= end) : (now >= start || now <= end);
    }

    /** Re-arm everything that was on — after a reboot or an app update. */
    static void rearmAll(Context ctx) {
        scheduleDaily(ctx);
        scheduleWater(ctx);
    }
}
