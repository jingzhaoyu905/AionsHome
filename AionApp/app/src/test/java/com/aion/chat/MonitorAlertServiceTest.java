package com.aion.chat;

import android.content.res.AssetManager;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import org.junit.Test;
import static org.junit.Assert.*;

public class MonitorAlertServiceTest {
    private static final class Service extends AionPushService {
        int playbackAttempts;
        @Override public AssetManager getAssets() {
            playbackAttempts++;
            throw new IllegalStateException("No audio device in unit tests");
        }
    }

    @Test public void serviceStartsBothMonitorCuesWithoutAWebViewInForegroundOrBackground() throws Exception {
        Service service = new Service();
        Field foreground = AionPushService.class.getDeclaredField("isForegroundActive");
        foreground.setAccessible(true);
        Field lastCapture = AionPushService.class.getDeclaredField("lastPhoneCaptureAt");
        lastCapture.setAccessible(true);
        Method receive = AionPushService.class.getDeclaredMethod("handleMessage", String.class);
        receive.setAccessible(true);
        int expected = 0;
        for (boolean active : new boolean[] {false, true}) {
            foreground.setBoolean(service, active);
            for (String type : new String[] {"monitor_alert", "cam_check"}) {
                // Avoid unrelated screenshot work in this audio routing test.
                lastCapture.setLong(service, System.currentTimeMillis());
                receive.invoke(service, "{\"type\":\"" + type + "\",\"data\":{}}");
                assertEquals("Service must attempt playback immediately: " + type,
                        ++expected, service.playbackAttempts);
                receive.invoke(service, "{\"type\":\"" + type
                        + "\",\"data\":{\"phone_camera_native_capture\":true}}");
                assertEquals("Phone capture owns its synchronized cue", expected, service.playbackAttempts);
            }
        }
    }
}
