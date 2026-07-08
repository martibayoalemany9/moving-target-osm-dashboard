package com.movingtarget.auto;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public final class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        int padding = (int) (20 * getResources().getDisplayMetrics().density);

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        layout.setPadding(padding, padding, padding, padding);

        TextView title = new TextView(this);
        title.setText("Moving Target OSM Android Auto");
        title.setTextSize(22);
        layout.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView help = new TextView(this);
        help.setText("Set the dashboard samples endpoint used by the Android Auto template. For DHU with the dashboard running on this Mac, use adb reverse tcp:8765 tcp:8765 and keep the default URL.");
        help.setTextSize(15);
        help.setPadding(0, padding / 2, 0, padding / 2);
        layout.addView(help);

        EditText urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setText(DashboardConfig.getSamplesUrl(this));
        layout.addView(urlInput, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        Button save = new Button(this);
        save.setText("Save endpoint");
        save.setOnClickListener(view -> {
            DashboardConfig.setSamplesUrl(this, urlInput.getText().toString());
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show();
        });
        layout.addView(save);

        setContentView(layout);
    }
}
