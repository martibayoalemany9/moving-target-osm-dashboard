package com.movingtarget.auto;

import androidx.annotation.NonNull;
import androidx.car.app.CarContext;
import androidx.car.app.Screen;
import androidx.car.app.model.Action;
import androidx.car.app.model.Header;
import androidx.car.app.model.Pane;
import androidx.car.app.model.PaneTemplate;
import androidx.car.app.model.Row;
import androidx.car.app.model.Template;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class MovingTargetScreen extends Screen {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private TelemetrySnapshot snapshot = TelemetrySnapshot.loading();
    private boolean refreshStarted;

    MovingTargetScreen(@NonNull CarContext carContext) {
        super(carContext);
    }

    @NonNull
    @Override
    public Template onGetTemplate() {
        if (!refreshStarted) {
            refreshStarted = true;
            refresh();
        }

        Pane.Builder pane = new Pane.Builder();
        pane.addRow(new Row.Builder()
                .setTitle(snapshot.title)
                .addText(snapshot.ok ? snapshot.latency : userFacingError())
                .build());

        if (snapshot.ok) {
            pane.addRow(new Row.Builder().setTitle("Radio").addText(snapshot.radio).build());
            pane.addRow(new Row.Builder().setTitle("Position").addText(snapshot.location).build());
            pane.addRow(new Row.Builder().setTitle("Route").addText(snapshot.route).build());
            pane.addRow(new Row.Builder().setTitle("Router").addText(snapshot.router).build());
            if (!snapshot.updated.isEmpty()) {
                pane.addRow(new Row.Builder().setTitle("Updated").addText(snapshot.updated).build());
            }
        }

        pane.addAction(new Action.Builder()
                .setTitle("Refresh")
                .setOnClickListener(this::refresh)
                .build());

        Header header = new Header.Builder()
                .setTitle("Moving Target OSM")
                .setStartHeaderAction(Action.APP_ICON)
                .build();

        return new PaneTemplate.Builder(pane.build())
                .setHeader(header)
                .build();
    }

    private String userFacingError() {
        if (snapshot.error == null || snapshot.error.isEmpty()) {
            return "Waiting for the local dashboard.";
        }
        return snapshot.error;
    }

    private void refresh() {
        String samplesUrl = DashboardConfig.getSamplesUrl(getCarContext());
        executor.execute(() -> {
            snapshot = TelemetryRepository.fetchLatest(samplesUrl);
            invalidate();
        });
    }
}
