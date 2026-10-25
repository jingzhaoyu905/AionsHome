package com.aion.chat;

import android.content.Context;
import android.content.ContextWrapper;
import android.media.MediaPlayer;
import android.webkit.ValueCallback;
import android.webkit.WebView;
import java.lang.reflect.Field;
import java.util.Map;
import java.util.Set;
import org.junit.Test;
import static org.junit.Assert.*;

public class TtsAudioBridgeTest {
    @Test @SuppressWarnings("unchecked")
    public void backgroundingReleasesIdleSpeechWithoutWaitingForJavascript() throws Exception {
        Context context = new ContextWrapper(null) {
            @Override public Context getApplicationContext() { return this; }
        };
        WebView webView = new WebView(context) {
            @Override public boolean post(Runnable action) { action.run(); return true; }
        };
        TtsAudioBridge bridge = new TtsAudioBridge(context, webView);
        Field ownersField = TtsAudioBridge.class.getDeclaredField("playbackOwners");
        ownersField.setAccessible(true);
        Map<String, Object> owners = (Map<String, Object>) ownersField.get(bridge);
        Object page = new Object(), background = new Object();
        java.util.List<String> started = new java.util.ArrayList<>();
        owners.put("tts-page", page);
        bridge.beginSpeech("tts-page");
        try {
            TtsPlaybackCoordinator.shared.request(page, () -> started.add("page"));
            TtsPlaybackCoordinator.shared.request(background, () -> started.add("background"));
            bridge.setForeground(false);
            assertEquals(java.util.Arrays.asList("page", "background"), started);
            assertTrue(owners.isEmpty());
            assertFalse(bridge.isForeground());
        } finally {
            bridge.shutdown();
            TtsPlaybackCoordinator.shared.release(page);
            TtsPlaybackCoordinator.shared.release(background);
        }
    }

    @Test
    public void resumedPageCannotReplayServiceOwnedMonitorCueOrAdvanceSpeech() {
        java.util.List<Runnable> pageTasks = new java.util.ArrayList<>();
        java.util.List<String> speech = new java.util.ArrayList<>();
        Context context = new ContextWrapper(null) {
            @Override public Context getApplicationContext() { return this; }
        };
        WebView webView = new WebView(context) {
            @Override public boolean post(Runnable action) { pageTasks.add(action); return true; }
            @Override public String getUrl() { return "https://home.test/chat"; }
            @Override public void evaluateJavascript(String script, ValueCallback<String> callback) {
            }
        };
        TtsAudioBridge bridge = new TtsAudioBridge(context, webView);
        Object current = new Object(), next = new Object();
        try {
            TtsPlaybackCoordinator.shared.request(current, () -> speech.add("current"));
            TtsPlaybackCoordinator.shared.request(next, () -> speech.add("next"));
            // Both cached and new pages must leave monitor audio to the service.
            bridge.play("tts-monitor-alert", "/public/AionMonitoralart.mp3");
            bridge.playMonitorAlert();
            assertTrue("A paused WebView must have no cue to replay on resume", pageTasks.isEmpty());
            assertEquals(java.util.Arrays.asList("current"), speech);
            TtsPlaybackCoordinator.shared.release(current);
            assertEquals(java.util.Arrays.asList("current", "next"), speech);
            assertTrue(pageTasks.isEmpty());
        } finally {
            bridge.shutdown();
            TtsPlaybackCoordinator.shared.release(current);
            TtsPlaybackCoordinator.shared.release(next);
        }
    }

    private static final class PreparedPlayer extends MediaPlayer {
        boolean playing, released;
        @Override public boolean isPlaying() { return playing; }
        @Override public void start() { playing = true; }
        @Override public void pause() {
            if (!playing) throw new IllegalStateException("Cannot pause a prepared player");
            playing = false;
        }
        @Override public void release() { released = true; }
    }

    @Test @SuppressWarnings("unchecked")
    public void pausingReadingQueuedBehindChatKeepsItsUnplayedSegment() throws Exception {
        Context context = new ContextWrapper(null) {
            @Override public Context getApplicationContext() { return this; }
        };
        WebView webView = new WebView(context) {
            @Override public boolean post(Runnable action) { action.run(); return true; }
            @Override public void evaluateJavascript(String script, ValueCallback<String> callback) {}
        };
        TtsAudioBridge bridge = new TtsAudioBridge(context, webView);
        PreparedPlayer player = new PreparedPlayer();
        Field players = TtsAudioBridge.class.getDeclaredField("players");
        players.setAccessible(true);
        ((Map<String, MediaPlayer>) players.get(bridge)).put("tts-reading", player);
        Field prepared = TtsAudioBridge.class.getDeclaredField("preparedPlayers");
        prepared.setAccessible(true);
        ((Set<String>) prepared.get(bridge)).add("tts-reading");
        Object chat = new Object();
        try {
            TtsPlaybackCoordinator.shared.request(chat, () -> {});
            bridge.resumeAudio("tts-reading");
            bridge.pauseAudio("tts-reading");
            assertFalse("Pausing queued reading must not discard it as an error", player.released);
            TtsPlaybackCoordinator.shared.release(chat);
            assertFalse("Paused reading must no longer be queued", player.playing);
            bridge.resumeAudio("tts-reading");
            assertTrue(player.playing);
            bridge.pauseAudio("tts-reading");
            assertFalse(player.playing);
        } finally {
            bridge.shutdown();
            TtsPlaybackCoordinator.shared.release(chat);
        }
    }
}
