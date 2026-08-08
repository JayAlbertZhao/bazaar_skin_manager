using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using HarmonyLib;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    /// <summary>
    /// Request-gated metadata tracer at the PlayVO boundary. This is the first
    /// boundary that exposes both the selected CardAudio route and the complete
    /// ordered FMOD label-parameter tuple. It never changes playback.
    /// </summary>
    internal static class RuntimeAudioTrace
    {
        private const string RequestFileName = "request.json";
        private const string ResultFileName = "events.ndjson";
        private static readonly object WriteLock = new object();
        private static bool _installed;

        private sealed class Selector
        {
            internal string Parameter;
            internal string Label;
        }

        internal static string AuditRoot()
        {
            return Path.Combine(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData),
                "BazaarSkinManager",
                "TheBazaar",
                "audio-audit");
        }

        internal static bool IsRequested()
        {
            string requestPath = Path.Combine(AuditRoot(), RequestFileName);
            if (!File.Exists(requestPath))
            {
                return false;
            }

            try
            {
                return Regex.IsMatch(
                    File.ReadAllText(requestPath),
                    "\"enabled\"\\s*:\\s*true",
                    RegexOptions.IgnoreCase |
                    RegexOptions.CultureInvariant);
            }
            catch (Exception exception)
            {
                Plugin.Log.LogWarning(
                    "Audio metadata trace request could not be read; " +
                    "leaving audio untouched: " + exception.Message);
                return false;
            }
        }

        internal static void Install(Harmony harmony)
        {
            if (_installed || harmony == null || !IsRequested())
            {
                return;
            }

            try
            {
                Type voPlayer = AccessTools.TypeByName("VOPlayer");
                if (voPlayer == null)
                {
                    throw new TypeLoadException("VOPlayer");
                }

                MethodInfo target = null;
                foreach (MethodInfo candidate in voPlayer.GetMethods(
                    BindingFlags.Instance |
                    BindingFlags.Public |
                    BindingFlags.NonPublic))
                {
                    ParameterInfo[] parameters = candidate.GetParameters();
                    if (candidate.Name == "PlayVO" &&
                        parameters.Length == 3 &&
                        parameters[0].ParameterType == typeof(bool) &&
                        parameters[1].ParameterType.FullName ==
                            "CardAudio+AudioHookType" &&
                        parameters[2].ParameterType.FullName ==
                            "System.ValueTuple`2[[System.String, mscorlib, " +
                            "Version=4.0.0.0, Culture=neutral, " +
                            "PublicKeyToken=b77a5c561934e089],[System.String, " +
                            "mscorlib, Version=4.0.0.0, Culture=neutral, " +
                            "PublicKeyToken=b77a5c561934e089]][]")
                    {
                        target = candidate;
                        break;
                    }
                }
                if (target == null)
                {
                    // Assembly-qualified generic names can vary by runtime;
                    // the first two exact parameters plus ValueTuple[] are
                    // sufficient for the validated 24001960/24570932 overload.
                    foreach (MethodInfo candidate in voPlayer.GetMethods(
                        BindingFlags.Instance |
                        BindingFlags.Public |
                        BindingFlags.NonPublic))
                    {
                        ParameterInfo[] parameters = candidate.GetParameters();
                        if (candidate.Name == "PlayVO" &&
                            parameters.Length == 3 &&
                            parameters[0].ParameterType == typeof(bool) &&
                            parameters[1].ParameterType.FullName ==
                                "CardAudio+AudioHookType" &&
                            parameters[2].ParameterType.IsArray &&
                            parameters[2].ParameterType.GetElementType()
                                .FullName.StartsWith(
                                    "System.ValueTuple`2",
                                    StringComparison.Ordinal))
                        {
                            target = candidate;
                            break;
                        }
                    }
                }
                if (target == null)
                {
                    throw new MissingMethodException(
                        "VOPlayer",
                        "PlayVO(bool, AudioHookType, ValueTuple<string,string>[])");
                }

                harmony.Patch(
                    target,
                    prefix: new HarmonyMethod(
                        typeof(RuntimeAudioTrace),
                        nameof(ObservePlayVo)));
                _installed = true;
                Plugin.Log.LogInfo(
                    "Request-gated Mak PlayVO metadata trace enabled. " +
                    "Playback remains unchanged.");
            }
            catch (Exception exception)
            {
                Plugin.Log.LogWarning(
                    "Audio metadata trace was not installed; playback " +
                    "remains unchanged: " + exception);
            }
        }

        private static void ObservePlayVo(object[] __args)
        {
            try
            {
                if (__args == null || __args.Length != 3 ||
                    __args[1] == null)
                {
                    return;
                }

                bool isHero = Convert.ToBoolean(
                    __args[0],
                    CultureInfo.InvariantCulture);
                int hookType = Convert.ToInt32(
                    __args[1],
                    CultureInfo.InvariantCulture);
                object cardAudio = ResolveSelectedCardAudio(isHero, hookType);
                UnityEngine.Object unityCardAudio =
                    cardAudio as UnityEngine.Object;
                if (unityCardAudio == null ||
                    !string.Equals(
                        unityCardAudio.name,
                        "MakAudioSO",
                        StringComparison.Ordinal))
                {
                    return;
                }

                object hook = FindHook(cardAudio, hookType);
                if (hook == null)
                {
                    return;
                }

                object eventReference = ReadMember(hook, "EventRef");
                object guid = eventReference == null
                    ? null
                    : ReadMember(eventReference, "Guid");
                if (guid == null)
                {
                    return;
                }

                string hookName = Convert.ToString(
                    ReadMember(hook, "AudioHookName"),
                    CultureInfo.InvariantCulture);
                List<Selector> selectors = ReadSelectors(__args[2]);
                WriteEvent(
                    CanonicalGuid(guid),
                    ResolveEventPath(eventReference),
                    hookName,
                    hookType,
                    selectors,
                    isHero);
            }
            catch (Exception exception)
            {
                // Void Harmony prefix: exceptions are contained and the
                // original PlayVO method always continues unchanged.
                Plugin.Log.LogWarning(
                    "Mak PlayVO metadata observation failed; original " +
                    "playback continues: " + exception.Message);
            }
        }

        private static object ResolveSelectedCardAudio(
            bool isHero,
            int hookType)
        {
            Type soundManagerType = AccessTools.TypeByName("SoundManager");
            Type servicesType =
                AccessTools.TypeByName("TheBazaar.AppFramework.Services");
            if (soundManagerType == null || servicesType == null)
            {
                return null;
            }

            object soundManager = null;
            foreach (MethodInfo method in servicesType.GetMethods(
                BindingFlags.Static |
                BindingFlags.Public |
                BindingFlags.NonPublic))
            {
                if (method.Name == "Get" &&
                    method.IsGenericMethodDefinition &&
                    method.GetGenericArguments().Length == 1 &&
                    method.GetParameters().Length == 0)
                {
                    soundManager = method.MakeGenericMethod(soundManagerType)
                        .Invoke(null, null);
                    break;
                }
            }
            object handler = ReadMember(soundManager, "CardAudioHandler");
            if (handler == null)
            {
                return null;
            }
            string field = isHero
                ? "HeroCardAudio"
                : hookType == 16
                    ? "RewardCardAudio"
                    : "ActiveCardAudio";
            return ReadMember(handler, field);
        }

        private static object FindHook(object cardAudio, int hookType)
        {
            IEnumerable hooks = ReadMember(cardAudio, "Hooks") as IEnumerable;
            if (hooks == null)
            {
                return null;
            }
            foreach (object hook in hooks)
            {
                object value = ReadMember(hook, "AudioHookType");
                if (value != null &&
                    Convert.ToInt32(value, CultureInfo.InvariantCulture) ==
                        hookType)
                {
                    return hook;
                }
            }
            return null;
        }

        private static List<Selector> ReadSelectors(object value)
        {
            List<Selector> result = new List<Selector>();
            IEnumerable tuples = value as IEnumerable;
            if (tuples == null)
            {
                return result;
            }
            foreach (object tuple in tuples)
            {
                string parameter = Convert.ToString(
                    ReadMember(tuple, "Item1"),
                    CultureInfo.InvariantCulture);
                string label = Convert.ToString(
                    ReadMember(tuple, "Item2"),
                    CultureInfo.InvariantCulture);
                result.Add(
                    new Selector
                    {
                        Parameter = parameter ?? string.Empty,
                        Label = label ?? string.Empty
                    });
            }
            return result;
        }

        private static object ReadMember(object value, string name)
        {
            if (value == null)
            {
                return null;
            }
            Type type = value.GetType();
            FieldInfo field = type.GetField(
                name,
                BindingFlags.Instance |
                BindingFlags.Public |
                BindingFlags.NonPublic);
            if (field != null)
            {
                return field.GetValue(value);
            }
            PropertyInfo property = type.GetProperty(
                name,
                BindingFlags.Instance |
                BindingFlags.Public |
                BindingFlags.NonPublic);
            return property == null ? null : property.GetValue(value, null);
        }

        private static uint ReadWord(object value, string name)
        {
            object member = ReadMember(value, name);
            return member == null
                ? 0U
                : unchecked((uint)Convert.ToInt32(
                    member,
                    CultureInfo.InvariantCulture));
        }

        private static string CanonicalGuid(object guid)
        {
            byte[] bytes = new byte[16];
            uint[] words =
            {
                ReadWord(guid, "Data1"),
                ReadWord(guid, "Data2"),
                ReadWord(guid, "Data3"),
                ReadWord(guid, "Data4")
            };
            for (int word = 0; word < words.Length; word++)
            {
                Buffer.BlockCopy(
                    BitConverter.GetBytes(words[word]),
                    0,
                    bytes,
                    word * 4,
                    4);
            }
            return new Guid(bytes).ToString("D");
        }

        private static string ResolveEventPath(object eventReference)
        {
            Type runtimeManager = AccessTools.TypeByName("FMODUnity.RuntimeManager");
            if (runtimeManager == null)
            {
                return string.Empty;
            }
            MethodInfo getDescription = AccessTools.Method(
                runtimeManager,
                "GetEventDescription",
                new[] { eventReference.GetType() });
            if (getDescription == null)
            {
                return string.Empty;
            }

            object description = getDescription.Invoke(
                null,
                new[] { eventReference });
            if (description == null)
            {
                return string.Empty;
            }
            MethodInfo getPath = AccessTools.Method(
                description.GetType(),
                "getPath");
            if (getPath == null)
            {
                return string.Empty;
            }
            object[] arguments = { null };
            getPath.Invoke(description, arguments);
            return arguments[0] as string ?? string.Empty;
        }

        private static string JsonEscape(string value)
        {
            StringBuilder escaped = new StringBuilder(
                (value ?? string.Empty).Length + 8);
            foreach (char character in value ?? string.Empty)
            {
                switch (character)
                {
                    case '\\': escaped.Append("\\\\"); break;
                    case '"': escaped.Append("\\\""); break;
                    case '\r': escaped.Append("\\r"); break;
                    case '\n': escaped.Append("\\n"); break;
                    case '\t': escaped.Append("\\t"); break;
                    default: escaped.Append(character); break;
                }
            }
            return escaped.ToString();
        }

        private static string LogicalSlot(
            string hookName,
            IList<Selector> selectors)
        {
            string prefix = (hookName ?? string.Empty).Replace(" ", "_");
            if (selectors.Count == 0)
            {
                return prefix + ".default";
            }
            StringBuilder value = new StringBuilder(prefix);
            foreach (Selector selector in selectors)
            {
                value.Append(".");
                value.Append(selector.Label);
            }
            return value.ToString();
        }

        private static string SelectorJson(IList<Selector> selectors)
        {
            StringBuilder value = new StringBuilder("[");
            for (int index = 0; index < selectors.Count; index++)
            {
                if (index != 0)
                {
                    value.Append(",");
                }
                value.Append("{\"parameter\":\"");
                value.Append(JsonEscape(selectors[index].Parameter));
                value.Append("\",\"label\":\"");
                value.Append(JsonEscape(selectors[index].Label));
                value.Append("\"}");
            }
            value.Append("]");
            return value.ToString();
        }

        private static void WriteEvent(
            string guid,
            string path,
            string hookName,
            int hookType,
            IList<Selector> selectors,
            bool isHero)
        {
            string directory = Path.Combine(AuditRoot(), "results");
            Directory.CreateDirectory(directory);
            string line = "{\"schema_version\":2," +
                "\"observed_at_utc\":\"" +
                DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture) +
                "\",\"observation_stage\":\"playvo_request\"," +
                "\"hero\":\"Mak\",\"category\":\"hero_voice\"," +
                "\"logical_slot\":\"" +
                JsonEscape(LogicalSlot(hookName, selectors)) +
                "\",\"hook_name\":\"" + JsonEscape(hookName) +
                "\",\"hook_type_value\":" +
                hookType.ToString(CultureInfo.InvariantCulture) +
                ",\"event_guid\":\"" + JsonEscape(guid) +
                "\",\"event_path\":\"" + JsonEscape(path) +
                "\",\"selectors\":" + SelectorJson(selectors) +
                ",\"is_hero_route\":" +
                (isHero ? "true" : "false") +
                ",\"playback_modified\":false}";
            lock (WriteLock)
            {
                File.AppendAllText(
                    Path.Combine(directory, ResultFileName),
                    line + Environment.NewLine,
                    new UTF8Encoding(false));
            }
        }
    }
}
