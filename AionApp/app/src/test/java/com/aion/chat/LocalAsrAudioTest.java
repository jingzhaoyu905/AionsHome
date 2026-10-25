package com.aion.chat;

import org.junit.Test;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import static org.junit.Assert.*;

public class LocalAsrAudioTest {
    @Test public void signedPcmAndWaveHeaderPreserveAudio() {
        byte[] pcm = {0, (byte) 0x80, (byte) 0xff, 0x7f, 0, 0};
        float[] samples = LocalAsrAudio.samples(pcm);
        assertArrayEquals(new float[]{-1f, 32767f / 32768f, 0f}, samples, 0f);
        byte[] wav = LocalAsrAudio.wav(pcm);
        ByteBuffer b = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN);
        assertEquals(16000, b.getInt(24));
        assertEquals(32000, b.getInt(28));
        assertEquals(pcm.length, b.getInt(40));
        assertEquals(44 + pcm.length, wav.length);
        assertArrayEquals(pcm, java.util.Arrays.copyOfRange(wav, 44, wav.length));
    }
}
