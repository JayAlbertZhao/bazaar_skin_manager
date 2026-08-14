using System.Collections.Generic;
using System.Runtime.Serialization;

namespace BazaarSkinManager.TheBazaar
{
    [DataContract]
    internal sealed class ModPackManifest
    {
        [DataMember(Name = "schema_version")]
        public int SchemaVersion;

        [DataMember(Name = "id")]
        public string Id;

        [DataMember(Name = "name")]
        public string Name;

        [DataMember(Name = "version")]
        public string Version;

        [DataMember(Name = "enabled")]
        public bool Enabled;

        [DataMember(Name = "target")]
        public PackTarget Target;

        [DataMember(Name = "visual_replacements")]
        public List<VisualReplacement> VisualReplacements;

        [DataMember(Name = "audio_manifest")]
        public string AudioManifest;

        [DataMember(Name = "animation")]
        public PackAnimation Animation;
    }

    [DataContract]
    internal sealed class PackAnimation
    {
        [DataMember(Name = "runtime_ready")]
        public bool RuntimeReady;

        [DataMember(Name = "suppress_visual_slots")]
        public List<string> SuppressVisualSlots;
    }

    [DataContract]
    internal sealed class PackTarget
    {
        [DataMember(Name = "game")]
        public string Game;

        [DataMember(Name = "hero")]
        public string Hero;

        [DataMember(Name = "skin")]
        public string Skin;

        [DataMember(Name = "skin_name_contains")]
        public string SkinNameContains;
    }

    [DataContract]
    internal sealed class VisualReplacement
    {
        [DataMember(Name = "slot")]
        public string Slot;

        [DataMember(Name = "file")]
        public string File;

        [DataMember(Name = "match_names")]
        public List<string> MatchNames;

        [DataMember(Name = "match_mode")]
        public string MatchMode;

        [DataMember(Name = "direct_only")]
        public bool DirectOnly;

        [DataMember(Name = "pixels_per_unit")]
        public float PixelsPerUnit;

        [DataMember(Name = "scale_multiplier")]
        public float ScaleMultiplier;

        [DataMember(Name = "deployment")]
        public VisualDeployment Deployment;
    }

    [DataContract]
    internal sealed class VisualDeployment
    {
        [DataMember(Name = "mode")]
        public string Mode;
    }

    [DataContract]
    internal sealed class AudioPackManifest
    {
        [DataMember(Name = "schema_version")]
        public int SchemaVersion;

        [DataMember(Name = "enabled")]
        public bool Enabled;

        [DataMember(Name = "target")]
        public AudioPackTarget Target;

        [DataMember(Name = "fallback")]
        public string Fallback;

        [DataMember(Name = "audio_format")]
        public AudioFormatContract AudioFormat;

        [DataMember(Name = "playback")]
        public AudioPlaybackContract Playback;

        [DataMember(Name = "routes")]
        public List<AudioRoute> Routes;
    }

    [DataContract]
    internal sealed class AudioPackTarget
    {
        [DataMember(Name = "game")]
        public string Game;

        [DataMember(Name = "steam_build")]
        public string SteamBuild;

        [DataMember(Name = "hero")]
        public string Hero;
    }

    [DataContract]
    internal sealed class AudioFormatContract
    {
        [DataMember(Name = "encoding")]
        public string Encoding;

        [DataMember(Name = "sample_rate_hz")]
        public int SampleRateHz;

        [DataMember(Name = "channels")]
        public int Channels;

        [DataMember(Name = "sample_width_bytes")]
        public int SampleWidthBytes;
    }

    [DataContract]
    internal sealed class AudioPlaybackContract
    {
        [DataMember(Name = "gain")]
        public float Gain;

        [DataMember(Name = "single_dialogue_channel")]
        public bool SingleDialogueChannel;
    }

    [DataContract]
    internal sealed class AudioRoute
    {
        [DataMember(Name = "logical_slot")]
        public string LogicalSlot;

        [DataMember(Name = "category")]
        public string Category;

        [DataMember(Name = "event_guid")]
        public string EventGuid;

        [DataMember(Name = "event_path")]
        public string EventPath;

        [DataMember(Name = "selectors")]
        public List<AudioSelector> Selectors;

        [DataMember(Name = "variants")]
        public List<AudioVariant> Variants;
    }

    [DataContract]
    internal sealed class AudioSelector
    {
        [DataMember(Name = "parameter")]
        public string Parameter;

        [DataMember(Name = "label")]
        public string Label;
    }

    [DataContract]
    internal sealed class AudioVariant
    {
        [DataMember(Name = "file")]
        public string File;

        [DataMember(Name = "sha256")]
        public string Sha256;

        [DataMember(Name = "weight")]
        public int Weight;

        [DataMember(Name = "sample_name")]
        public string SampleName;
    }
}
