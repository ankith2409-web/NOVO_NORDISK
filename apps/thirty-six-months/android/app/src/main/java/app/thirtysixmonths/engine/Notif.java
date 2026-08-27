package app.thirtysixmonths.engine;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/** One notification channel per kind, and a single way to post. */
final class Notif {

    static final String CH_REMINDER = "reminder";
    static final String CH_WATER = "water";

    private Notif() {}

    static void ensureChannels(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) return;

        NotificationChannel study = new NotificationChannel(
                CH_REMINDER, "Study reminder", NotificationManager.IMPORTANCE_DEFAULT);
        study.setDescription("The daily nudge to start your deep block.");
        nm.createNotificationChannel(study);

        // Water fires often, so it stays quiet by design.
        NotificationChannel water = new NotificationChannel(
                CH_WATER, "Water", NotificationManager.IMPORTANCE_LOW);
        water.setDescription("Hourly hydration nudges between waking and lights out.");
        water.setShowBadge(false);
        nm.createNotificationChannel(water);
    }

    static void post(Context ctx, String channel, int id, String title, String body) {
        ensureChannels(ctx);
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) return;

        Intent open = new Intent(ctx, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(
                ctx, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

        Notification.Builder b = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(ctx, channel)
                : new Notification.Builder(ctx);

        Notification n = b.setContentTitle(title)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setSmallIcon(R.drawable.ic_stat)
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build();

        try {
            nm.notify(id, n);
        } catch (SecurityException ignored) {
            // Notifications are switched off for the app; nothing to do here.
        }
    }
}
