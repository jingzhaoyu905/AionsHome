package com.aion.chat;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

/**
 * 原生麦克风桥接 — 绕过 WebView getUserMedia 的 HTTPS 限制
 * 录音线程实时采集 PCM，通过 evaluateJavascript 推送给前端 JS
 *
 * 前端调用: window.AionAudio.start() / stop() / isRecording()
 * 前端接收: remoteVoice._onNativeChunk(base64) / _ambientNativeOnChunk(base64)
 */
public class AudioBridge {

    private static final int SAMPLE_RATE = 16000;
    private static final int CHANNEL = AudioFormat.CHANNEL_IN_MONO;
    private static final int ENCODING = AudioFormat.ENCODING_PCM_16BIT;
    // 40ms 一帧: 16000 * 0.04 * 2bytes = 1280
    private static final int FRAME_BYTES = 1280;

    private final WebView webView;
    private AudioRecord recorder;
    private volatile boolean recording = false;

    // 视频录制桥接 — 录制时同步推送音频帧
    private volatile VideoBridge videoBridge;

    public AudioBridge(WebView webView) {
        this.webView = webView;
    }

    /** 设置视频录制桥接，录制时自动推送 PCM 帧 */
    public void setVideoBridge(VideoBridge vb) {
        this.videoBridge = vb;
    }

    interface PcmListener {
        void onPcm(byte[] data);
        void onStopped();
    }
    private PcmListener owner;
    private Thread captureThread;

    @JavascriptInterface
    public boolean start() { return startCapture(null); }

    synchronized boolean startCapture(PcmListener listener) {
        if (recorder != null) return false;
        int minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL, ENCODING);
        AudioRecord capture;
        try {
            capture = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE, CHANNEL, ENCODING, Math.max(minBuf, FRAME_BYTES * 4));
            if (capture.getState() != AudioRecord.STATE_INITIALIZED) { capture.release(); return false; }
            try { capture.startRecording(); }
            catch (RuntimeException e) { capture.release(); return false; }
        } catch (RuntimeException e) { return false; }
        recorder = capture;
        owner = listener;
        recording = true;
        captureThread = new Thread(() -> {
            try {
                byte[] buf = new byte[FRAME_BYTES];
                while (recording) {
                    int read = capture.read(buf, 0, buf.length);
                    if (read <= 0) break;
                    if (listener != null) {
                        listener.onPcm(java.util.Arrays.copyOf(buf, read));
                        continue;
                    }
                    VideoBridge vb = videoBridge;
                    if (vb != null && vb.isRecording()) vb.onAudioFrame(buf, read);
                    String b64 = Base64.encodeToString(buf, 0, read, Base64.NO_WRAP);
                    webView.post(() -> {
                        if (recording && recorder == capture) {
                            webView.evaluateJavascript(
                                    "typeof remoteVoice!=='undefined'&&remoteVoice._onNativeChunk('" + b64 + "');" +
                                    "typeof videoCall!=='undefined'&&videoCall._onNativeChunk&&videoCall._onNativeChunk('" + b64 + "');" +
                                    "typeof _ambientNativeOnChunk==='function'&&_ambientNativeOnChunk('" + b64 + "');" +
                                    "typeof _voiceNativeOnChunk==='function'&&_voiceNativeOnChunk('" + b64 + "')", null);
                        }
                    });
                }
            } finally {
                try { capture.stop(); } catch (Exception ignored) { }
                capture.release();
                synchronized (AudioBridge.this) {
                    if (recorder == capture) { recording = false; recorder = null; owner = null; }
                }
                if (listener != null) listener.onStopped();
            }
        }, "AionMicThread");
        captureThread.start();
        return true;
    }

    @JavascriptInterface
    public void stop() { stopCapture(null); }

    void stopCapture(PcmListener listener) {
        Thread thread;
        synchronized (this) {
            if (recorder == null || owner != listener) return;
            recording = false;
            try { recorder.stop(); } catch (Exception ignored) { }
            thread = captureThread;
        }
        // Do not hold the bridge lock while capture's finally block releases the recorder.
        if (thread != null && thread != Thread.currentThread()) {
            try { thread.join(1000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
    }

    @JavascriptInterface
    public boolean isRecording() {
        return recording;
    }
}
