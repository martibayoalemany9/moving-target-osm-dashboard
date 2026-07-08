package com.movingtarget.auto;

import android.content.Context;
import android.content.SharedPreferences;

final class DashboardConfig {
    private static final String PREFS = "moving_target_auto";
    private static final String SAMPLES_URL = "samples_url";

    private DashboardConfig() {
    }

    static String getSamplesUrl(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return prefs.getString(SAMPLES_URL, context.getString(R.string.default_samples_url));
    }

    static void setSamplesUrl(Context context, String url) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString(SAMPLES_URL, url.trim())
                .apply();
    }
}
