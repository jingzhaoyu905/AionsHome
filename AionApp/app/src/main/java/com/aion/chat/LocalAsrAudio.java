package com.aion.chat;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

final class LocalAsrAudio {
    static float[] samples(byte[] pcm) {
        float[] result = new float[pcm.length / 2];
        for (int i = 0; i < result.length; i++) {
            result[i] = (short) ((pcm[2*i] & 255) | (pcm[2*i+1] << 8)) / 32768f;
        }
        return result;
    }

    static byte[] wav(byte[] pcm) {
        ByteBuffer b = ByteBuffer.allocate(44 + pcm.length).order(ByteOrder.LITTLE_ENDIAN);
        b.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + pcm.length);
        b.put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16);
        b.putShort((short) 1).putShort((short) 1).putInt(16000).putInt(32000);
        b.putShort((short) 2).putShort((short) 16);
        b.put("data".getBytes(StandardCharsets.US_ASCII)).putInt(pcm.length).put(pcm);
        return b.array();
    }
}
