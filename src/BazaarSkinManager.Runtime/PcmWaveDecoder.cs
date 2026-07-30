using System;
using System.IO;
using System.Text;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class PcmWave
    {
        internal int Channels;
        internal int SampleRate;
        internal float[] Samples;
    }

    internal static class PcmWaveDecoder
    {
        private const int MaximumPcmBytes = 64 * 1024 * 1024;

        internal static PcmWave Decode(byte[] bytes)
        {
            if (bytes == null || bytes.Length < 44)
            {
                throw new InvalidDataException("WAV is too short.");
            }

            using (MemoryStream stream = new MemoryStream(bytes, false))
            using (BinaryReader reader = new BinaryReader(
                stream,
                Encoding.ASCII,
                true))
            {
                RequireFourCc(reader, "RIFF");
                reader.ReadUInt32();
                RequireFourCc(reader, "WAVE");

                ushort format = 0;
                ushort channels = 0;
                uint sampleRate = 0;
                ushort blockAlign = 0;
                ushort bitsPerSample = 0;
                byte[] pcm = null;

                while (stream.Position + 8 <= stream.Length)
                {
                    string chunk = FourCc(reader);
                    uint chunkSize = reader.ReadUInt32();
                    long payloadStart = stream.Position;
                    long payloadEnd = payloadStart + chunkSize;
                    if (payloadEnd > stream.Length)
                    {
                        throw new InvalidDataException(
                            "WAV contains a truncated " + chunk + " chunk.");
                    }

                    if (chunk == "fmt ")
                    {
                        if (chunkSize < 16)
                        {
                            throw new InvalidDataException(
                                "WAV fmt chunk is shorter than 16 bytes.");
                        }
                        format = reader.ReadUInt16();
                        channels = reader.ReadUInt16();
                        sampleRate = reader.ReadUInt32();
                        reader.ReadUInt32();
                        blockAlign = reader.ReadUInt16();
                        bitsPerSample = reader.ReadUInt16();
                    }
                    else if (chunk == "data")
                    {
                        if (chunkSize == 0 || chunkSize > MaximumPcmBytes)
                        {
                            throw new InvalidDataException(
                                "WAV PCM payload has an invalid size.");
                        }
                        pcm = reader.ReadBytes(checked((int)chunkSize));
                        if (pcm.Length != (int)chunkSize)
                        {
                            throw new InvalidDataException(
                                "WAV PCM payload is truncated.");
                        }
                    }

                    stream.Position = payloadEnd + (chunkSize & 1U);
                }

                if (format != 1)
                {
                    throw new InvalidDataException(
                        "Only integer PCM WAV is supported.");
                }
                if (channels != 1)
                {
                    throw new InvalidDataException(
                        "Voice WAV must be mono.");
                }
                if (sampleRate < 8000 || sampleRate > 192000)
                {
                    throw new InvalidDataException(
                        "Voice WAV sample rate is out of range.");
                }
                if (bitsPerSample != 16 || blockAlign != channels * 2)
                {
                    throw new InvalidDataException(
                        "Voice WAV must be 16-bit PCM.");
                }
                if (pcm == null || pcm.Length % blockAlign != 0)
                {
                    throw new InvalidDataException(
                        "Voice WAV lacks aligned PCM data.");
                }

                float[] samples = new float[pcm.Length / 2];
                for (int index = 0; index < samples.Length; index++)
                {
                    short value = (short)(
                        pcm[index * 2] |
                        (pcm[index * 2 + 1] << 8));
                    samples[index] = value / 32768f;
                }
                return new PcmWave
                {
                    Channels = channels,
                    SampleRate = checked((int)sampleRate),
                    Samples = samples
                };
            }
        }

        internal static AudioClip CreateClip(string name, byte[] bytes)
        {
            PcmWave wave = Decode(bytes);
            int frames = wave.Samples.Length / wave.Channels;
            AudioClip clip = AudioClip.Create(
                name,
                frames,
                wave.Channels,
                wave.SampleRate,
                false);
            if (clip == null || !clip.SetData(wave.Samples, 0))
            {
                if (clip != null)
                {
                    UnityEngine.Object.Destroy(clip);
                }
                throw new InvalidDataException(
                    "Unity failed to create the decoded AudioClip.");
            }
            UnityEngine.Object.DontDestroyOnLoad(clip);
            return clip;
        }

        private static void RequireFourCc(BinaryReader reader, string expected)
        {
            string actual = FourCc(reader);
            if (actual != expected)
            {
                throw new InvalidDataException(
                    "Expected " + expected + ", got " + actual + ".");
            }
        }

        private static string FourCc(BinaryReader reader)
        {
            byte[] value = reader.ReadBytes(4);
            if (value.Length != 4)
            {
                throw new EndOfStreamException();
            }
            return Encoding.ASCII.GetString(value);
        }
    }
}
