package app.thirtysixmonths.engine;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebViewAssetLoader;

import java.io.File;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

public class MainActivity extends Activity {

    /**
     * Assets are served over https://appassets.androidplatform.net/ rather than
     * file:// so the page gets a normal web origin. localStorage — which holds
     * every log, tick and setting — behaves predictably there; on a file://
     * origin it is a well-known source of quietly lost data.
     */
    private static final String ASSET_HOST = "appassets.androidplatform.net";
    private static final String START_URL = "https://" + ASSET_HOST + "/assets/index.html";
    private static final int RC_NOTIFICATIONS = 77;

    private WebView web;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);

        getWindow().setStatusBarColor(Color.parseColor("#E9E7E0"));
        getWindow().setNavigationBarColor(Color.parseColor("#E9E7E0"));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            // The page is light, so the system icons have to be dark.
            View decor = getWindow().getDecorView();
            int flags = decor.getSystemUiVisibility() | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            }
            decor.setSystemUiVisibility(flags);
        }

        Notif.ensureChannels(this);

        final WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
                .setDomain(ASSET_HOST)
                .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);

        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setBackgroundColor(Color.parseColor("#E9E7E0"));
        web.addJavascriptInterface(new Bridge(), "AndroidBridge");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest req) {
                return loader.shouldInterceptRequest(req.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (ASSET_HOST.equals(u.getHost())) return false;
                // Anything genuinely external belongs in a browser, not in here.
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }
        });

        setContentView(web);

        if (saved != null) {
            web.restoreState(saved);
        } else {
            web.loadUrl(START_URL);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    public void onBackPressed() {
        // Let the page take the press first (plan view falls back to today).
        web.evaluateJavascript(
                "(function(){try{return !!(window.onAndroidBack && window.onAndroidBack())}"
                        + "catch(e){return false}})()",
                value -> {
                    if (!"true".equals(value)) {
                        if (web.canGoBack()) web.goBack(); else finish();
                    }
                });
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    private boolean notificationsAllowed() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true;
        return checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
    }

    /**
     * The JavaScript bridge.
     *
     * Every method here runs on a WebView worker thread, never the main
     * thread — so anything touching the window or asking for a permission has
     * to be posted back with runOnUiThread.
     */
    private final class Bridge {

        @JavascriptInterface
        public String appVersion() {
            return BuildConfig.VERSION_NAME;
        }

        @JavascriptInterface
        public boolean hasNotifPermission() {
            return notificationsAllowed();
        }

        @JavascriptInterface
        public void requestNotifPermission() {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return;
            if (notificationsAllowed()) return;
            runOnUiThread(() -> requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS}, RC_NOTIFICATIONS));
        }

        @JavascriptInterface
        public void setDailyReminder(boolean on, int hour, int minute) {
            Reminders.setDaily(MainActivity.this, on, hour, minute);
        }

        @JavascriptInterface
        public void setWaterReminder(boolean on, int startMin, int endMin) {
            Reminders.setWater(MainActivity.this, on, startMin, endMin);
        }

        @JavascriptInterface
        public void notifyNow(String title, String body) {
            Notif.post(MainActivity.this, Notif.CH_REMINDER, 3, title, body);
        }

        @JavascriptInterface
        public void keepAwake(final boolean on) {
            runOnUiThread(() -> {
                if (on) {
                    getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                } else {
                    getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                }
            });
        }

        /** Writes the backup where a file manager can actually find it. */
        @JavascriptInterface
        public String exportBackup(String name, String json) {
            try {
                byte[] bytes = json.getBytes(StandardCharsets.UTF_8);

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
                    cv.put(MediaStore.MediaColumns.MIME_TYPE, "application/json");
                    cv.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                    Uri uri = getContentResolver()
                            .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                    if (uri == null) return "Could not write to Downloads.";
                    try (OutputStream out = getContentResolver().openOutputStream(uri)) {
                        if (out == null) return "Could not write to Downloads.";
                        out.write(bytes);
                    }
                    return "Saved to Downloads/" + name;
                }

                File dir = getExternalFilesDir(null);
                if (dir == null) return "No storage available for the backup.";
                File f = new File(dir, name);
                Files.write(f.toPath(), bytes);
                return "Saved to " + f.getAbsolutePath();

            } catch (Exception e) {
                return "Export failed: " + e.getMessage();
            }
        }
    }
}
