package app.thirtysixmonths.engine;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Posts the nudge, then books the next one. */
public class ReminderReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context ctx, Intent intent) {
        String action = intent.getAction();
        if (action == null) return;

        if (Reminders.ACTION_DAILY.equals(action)) {
            Notif.post(ctx, Notif.CH_REMINDER, 1,
                    "Deep block",
                    "Your best window is the next two hours. Phone in another room.");
            Reminders.scheduleDaily(ctx);

        } else if (Reminders.ACTION_WATER.equals(action)) {
            if (Reminders.waterWindowOpen(ctx)) {
                Notif.post(ctx, Notif.CH_WATER, 2, "Water", "Have a glass now.");
            }
            // Re-arm either way, so the chain survives the quiet hours.
            Reminders.scheduleWater(ctx);
        }
    }
}
