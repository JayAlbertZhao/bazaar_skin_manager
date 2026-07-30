using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    internal static class RuntimeAudioReplacement
    {
        private static AudioSource _source;
        private static object _voiceOwner;
        private static bool _voicePlayback;
        private static bool _installed;
        private static readonly HashSet<string> ReportedRoutes =
            new HashSet<string>(StringComparer.Ordinal);

        [ThreadStatic]
        private static int _characterSelectContext;

        [ThreadStatic]
        private static int _equipMusicContext;

        internal static void Initialize(GameObject host)
        {
            if (host == null || _source != null)
            {
                return;
            }
            _source = host.AddComponent<AudioSource>();
            _source.playOnAwake = false;
            _source.loop = false;
            _source.spatialBlend = 0f;
            _source.dopplerLevel = 0f;
            _source.priority = 32;
            _source.ignoreListenerPause = false;
        }

        internal static void Install(Harmony harmony)
        {
            if (_installed ||
                harmony == null ||
                Plugin.ActivePack == null ||
                Plugin.ActivePack.Audio == null)
            {
                return;
            }

            try
            {
                Type voPlayer = RequireType("VOPlayer");
                MethodInfo playVo = FindPlayVo(voPlayer);
                MethodInfo state = AccessTools.Method(
                    voPlayer,
                    "GetVOPlaybackState");
                Type menu = RequireType("TheBazaar.SoundMenuHandler");
                MethodInfo setHero = AccessTools.Method(menu, "SetHero");
                MethodInfo equip = AccessTools.Method(
                    menu,
                    "PlayEquipCollectionsVO");
                Type sfxPlayer = RequireType("SFXPlayer");
                MethodInfo playSfx = FindPlayOneShot(sfxPlayer);
                if (playVo == null ||
                    state == null ||
                    setHero == null ||
                    equip == null ||
                    playSfx == null)
                {
                    throw new MissingMethodException(
                        "One or more exact audio interception methods are absent.");
                }

                harmony.Patch(
                    playVo,
                    prefix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(ReplacePlayVo)));
                harmony.Patch(
                    state,
                    postfix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(ReportExternalVoState)));
                harmony.Patch(
                    setHero,
                    prefix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(BeginCharacterSelect)),
                    postfix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(EndCharacterSelect)),
                    finalizer: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(FinalizeCharacterSelect)));
                harmony.Patch(
                    equip,
                    prefix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(BeginEquipMusic)),
                    postfix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(EndEquipMusic)),
                    finalizer: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(FinalizeEquipMusic)));
                harmony.Patch(
                    playSfx,
                    prefix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(ReplaceMenuSfx)));
                _installed = true;
                Plugin.Log.LogInfo(
                    "External voice replacement enabled for exact Mak hero, " +
                    "Mak merchant, and Mak menu routes.");
            }
            catch (Exception exception)
            {
                Plugin.Log.LogWarning(
                    "Audio replacement hooks were not installed; original " +
                    "FMOD playback remains active: " + exception);
            }
        }

        internal static void Remove()
        {
            if (_source != null)
            {
                _source.Stop();
                UnityEngine.Object.Destroy(_source);
            }
            _source = null;
            _voiceOwner = null;
            _voicePlayback = false;
            _installed = false;
            ReportedRoutes.Clear();
            _characterSelectContext = 0;
            _equipMusicContext = 0;
        }

        private static bool ReplacePlayVo(
            object __instance,
            object[] __args)
        {
            try
            {
                if (__instance == null ||
                    __args == null ||
                    __args.Length != 3 ||
                    __args[1] == null)
                {
                    return true;
                }

                bool isHero = Convert.ToBoolean(
                    __args[0],
                    CultureInfo.InvariantCulture);
                int hookType = Convert.ToInt32(
                    __args[1],
                    CultureInfo.InvariantCulture);
                object cardAudio = ResolveSelectedCardAudio(
                    isHero,
                    hookType);
                UnityEngine.Object unityCardAudio =
                    cardAudio as UnityEngine.Object;
                if (unityCardAudio == null)
                {
                    return true;
                }
                string expectedAudio = isHero
                    ? "MakAudioSO"
                    : "MakMerchantAudioSO";
                if (!string.Equals(
                    unityCardAudio.name,
                    expectedAudio,
                    StringComparison.Ordinal))
                {
                    return true;
                }

                object hook = FindHook(cardAudio, hookType);
                if (hook == null)
                {
                    return true;
                }
                object eventReference = ReadMember(hook, "EventRef");
                object guid = eventReference == null
                    ? null
                    : ReadMember(eventReference, "Guid");
                if (guid == null)
                {
                    return true;
                }

                List<AudioSelector> selectors = ReadSelectors(__args[2]);
                LoadedAudioRoute route;
                if (!Plugin.ActivePack.Audio.TryRoute(
                    CanonicalGuid(guid),
                    selectors,
                    out route))
                {
                    return true;
                }
                if ((isHero && route.Category != "hero_voice") ||
                    (!isHero && route.Category != "merchant_voice"))
                {
                    return true;
                }

                // Mirror every guard that precedes EventInstance creation in
                // build 24001960. If an unknown state cannot be proven safe,
                // fail open to the original method.
                if (ReadBoolean(__instance, "IsSilentEncounter") && !isHero)
                {
                    return true;
                }
                if (InvokeBoolean(__instance, "IsTutorialVOPlaying"))
                {
                    return true;
                }
                if (!ReadBoolean(__instance, "NonTutorialVOEnabled"))
                {
                    return true;
                }
                if (IsReplayActive())
                {
                    return true;
                }
                if (ReadStaticBoolean(__instance.GetType(), "LateCardAudioLoad"))
                {
                    return true;
                }
                if (!InvokeBoolean(
                    __instance,
                    "CanPlayByPercentage",
                    hook))
                {
                    // The original method would return without playback after
                    // the same percentage roll.
                    return false;
                }

                if (hookType != 0 && hookType != 16)
                {
                    bool interruptible = InvokeBoolean(
                        __instance,
                        "InterruptVO");
                    if (!interruptible)
                    {
                        if (hookType == 8 ||
                            hookType == 9 ||
                            hookType == 10)
                        {
                            ForceStopCurrentVo(__instance);
                        }
                        else
                        {
                            return false;
                        }
                    }
                }

                if (!Play(route, __instance, true))
                {
                    return true;
                }
                ReportFirstPlayback(route);
                return false;
            }
            catch (Exception exception)
            {
                Plugin.Log.LogWarning(
                    "Exact voice replacement failed before suppression; " +
                    "the original FMOD event will play: " +
                    exception.Message);
                return true;
            }
        }

        private static void ReportExternalVoState(
            object __instance,
            ref FMOD.Studio.PLAYBACK_STATE __result)
        {
            if (_voicePlayback &&
                ReferenceEquals(_voiceOwner, __instance) &&
                _source != null &&
                _source.isPlaying)
            {
                __result = FMOD.Studio.PLAYBACK_STATE.PLAYING;
            }
        }

        private static void BeginCharacterSelect(
            object[] __args,
            ref bool __state)
        {
            __state = __args != null &&
                __args.Length == 1 &&
                string.Equals(
                    Convert.ToString(
                        __args[0],
                        CultureInfo.InvariantCulture),
                    "Mak",
                    StringComparison.Ordinal);
            if (__state)
            {
                _characterSelectContext++;
            }
        }

        private static void EndCharacterSelect(bool __state)
        {
            if (__state && _characterSelectContext > 0)
            {
                _characterSelectContext--;
            }
        }

        private static Exception FinalizeCharacterSelect(
            Exception __exception,
            bool __state)
        {
            if (__exception != null)
            {
                EndCharacterSelect(__state);
            }
            return __exception;
        }

        private static void BeginEquipMusic(
            object __instance,
            ref bool __state)
        {
            object collectionManager = ReadMember(
                __instance,
                "_collectionManager");
            object hero = ReadMember(collectionManager, "_currentHero");
            __state = string.Equals(
                Convert.ToString(hero, CultureInfo.InvariantCulture),
                "Mak",
                StringComparison.Ordinal);
            if (__state)
            {
                _equipMusicContext++;
            }
        }

        private static void EndEquipMusic(bool __state)
        {
            if (__state && _equipMusicContext > 0)
            {
                _equipMusicContext--;
            }
        }

        private static Exception FinalizeEquipMusic(
            Exception __exception,
            bool __state)
        {
            if (__exception != null)
            {
                EndEquipMusic(__state);
            }
            return __exception;
        }

        private static bool ReplaceMenuSfx(object[] __args)
        {
            try
            {
                if ((_characterSelectContext <= 0 &&
                    _equipMusicContext <= 0) ||
                    __args == null ||
                    __args.Length < 1 ||
                    __args[0] == null)
                {
                    return true;
                }
                object guid = ReadMember(__args[0], "Guid");
                if (guid == null)
                {
                    return true;
                }
                LoadedAudioRoute route;
                if (!Plugin.ActivePack.Audio.TryRoute(
                    CanonicalGuid(guid),
                    new List<AudioSelector>(),
                    out route) ||
                    route.Category != "menu_voice")
                {
                    return true;
                }
                bool contextMatches =
                    (_characterSelectContext > 0 &&
                        route.LogicalSlot == "Menu.CharacterSelect") ||
                    (_equipMusicContext > 0 &&
                        route.LogicalSlot == "Menu.EquipMusic");
                if (!contextMatches || !Play(route, null, false))
                {
                    return true;
                }
                ReportFirstPlayback(route);
                return false;
            }
            catch (Exception exception)
            {
                Plugin.Log.LogWarning(
                    "Menu voice replacement failed before suppression; " +
                    "the original FMOD event will play: " +
                    exception.Message);
                return true;
            }
        }

        private static bool Play(
            LoadedAudioRoute route,
            object voiceOwner,
            bool voicePlayback)
        {
            if (_source == null ||
                route == null ||
                Plugin.ActivePack == null ||
                Plugin.ActivePack.Audio == null)
            {
                return false;
            }
            LoadedAudioVariant variant =
                Plugin.ActivePack.Audio.Choose(route);
            if (variant == null || variant.Clip == null)
            {
                return false;
            }

            _source.Stop();
            _source.clip = variant.Clip;
            _source.volume = Plugin.ActivePack.Audio.Gain;
            _source.Play();
            if (!_source.isPlaying)
            {
                _source.clip = null;
                return false;
            }
            _voiceOwner = voiceOwner;
            _voicePlayback = voicePlayback;
            return true;
        }

        private static void ReportFirstPlayback(LoadedAudioRoute route)
        {
            string identity = route.Category + "/" + route.LogicalSlot;
            lock (ReportedRoutes)
            {
                if (!ReportedRoutes.Add(identity))
                {
                    return;
                }
            }
            Plugin.Log.LogInfo(
                "Confirmed first external voice playback for " +
                identity + ".");
        }

        private static MethodInfo FindPlayVo(Type voPlayer)
        {
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
                    return candidate;
                }
            }
            return null;
        }

        private static MethodInfo FindPlayOneShot(Type sfxPlayer)
        {
            foreach (MethodInfo candidate in sfxPlayer.GetMethods(
                BindingFlags.Instance |
                BindingFlags.Public |
                BindingFlags.NonPublic))
            {
                ParameterInfo[] parameters = candidate.GetParameters();
                if (candidate.Name == "PlayOneShotSfx" &&
                    parameters.Length == 2 &&
                    parameters[0].ParameterType.FullName ==
                        "FMODUnity.EventReference" &&
                    parameters[1].ParameterType == typeof(Vector3))
                {
                    return candidate;
                }
            }
            return null;
        }

        private static Type RequireType(string name)
        {
            Type type = AccessTools.TypeByName(name);
            if (type == null)
            {
                throw new TypeLoadException(name);
            }
            return type;
        }

        private static object ResolveSelectedCardAudio(
            bool isHero,
            int hookType)
        {
            Type soundManagerType = RequireType("SoundManager");
            Type servicesType = RequireType(
                "TheBazaar.AppFramework.Services");
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
            if (soundManager == null)
            {
                throw new InvalidOperationException(
                    "SoundManager service is unavailable.");
            }
            object handler = ReadMember(soundManager, "CardAudioHandler");
            if (handler == null)
            {
                throw new InvalidOperationException(
                    "CardAudioHandler is unavailable.");
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
                    Convert.ToInt32(
                        value,
                        CultureInfo.InvariantCulture) == hookType)
                {
                    return hook;
                }
            }
            return null;
        }

        private static List<AudioSelector> ReadSelectors(object value)
        {
            List<AudioSelector> selectors = new List<AudioSelector>();
            IEnumerable tuples = value as IEnumerable;
            if (tuples == null)
            {
                return selectors;
            }
            foreach (object tuple in tuples)
            {
                selectors.Add(
                    new AudioSelector
                    {
                        Parameter = Convert.ToString(
                            ReadMember(tuple, "Item1"),
                            CultureInfo.InvariantCulture) ?? string.Empty,
                        Label = Convert.ToString(
                            ReadMember(tuple, "Item2"),
                            CultureInfo.InvariantCulture) ?? string.Empty
                    });
            }
            return selectors;
        }

        private static bool ReadBoolean(object value, string name)
        {
            object member = ReadMember(value, name);
            if (member == null)
            {
                throw new MissingMemberException(
                    value.GetType().FullName,
                    name);
            }
            return Convert.ToBoolean(
                member,
                CultureInfo.InvariantCulture);
        }

        private static bool ReadStaticBoolean(Type type, string name)
        {
            FieldInfo field = type.GetField(
                name,
                BindingFlags.Static |
                BindingFlags.Public |
                BindingFlags.NonPublic);
            if (field == null)
            {
                throw new MissingFieldException(type.FullName, name);
            }
            return Convert.ToBoolean(
                field.GetValue(null),
                CultureInfo.InvariantCulture);
        }

        private static bool InvokeBoolean(
            object instance,
            string methodName,
            params object[] arguments)
        {
            MethodInfo method = AccessTools.Method(
                instance.GetType(),
                methodName,
                Array.ConvertAll(
                    arguments ?? new object[0],
                    value => value.GetType()));
            if (method == null)
            {
                foreach (MethodInfo candidate in instance.GetType().GetMethods(
                    BindingFlags.Instance |
                    BindingFlags.Public |
                    BindingFlags.NonPublic))
                {
                    if (candidate.Name == methodName &&
                        candidate.GetParameters().Length ==
                            (arguments == null ? 0 : arguments.Length))
                    {
                        method = candidate;
                        break;
                    }
                }
            }
            if (method == null)
            {
                throw new MissingMethodException(
                    instance.GetType().FullName,
                    methodName);
            }
            return Convert.ToBoolean(
                method.Invoke(instance, arguments),
                CultureInfo.InvariantCulture);
        }

        private static void ForceStopCurrentVo(object instance)
        {
            object dialogue = ReadMember(instance, "DialogueVOEvent");
            MethodInfo stop = null;
            foreach (MethodInfo method in instance.GetType().GetMethods(
                BindingFlags.Instance |
                BindingFlags.Public |
                BindingFlags.NonPublic))
            {
                if (method.Name == "StopCurrentVO" &&
                    method.GetParameters().Length == 1)
                {
                    stop = method;
                    break;
                }
            }
            if (stop == null)
            {
                throw new MissingMethodException(
                    instance.GetType().FullName,
                    "StopCurrentVO");
            }
            stop.Invoke(instance, new[] { dialogue });
        }

        private static bool IsReplayActive()
        {
            Type appState = RequireType("TheBazaar.AppState");
            PropertyInfo current = appState.GetProperty(
                "CurrentState",
                BindingFlags.Static |
                BindingFlags.Public |
                BindingFlags.NonPublic);
            Type extensions = RequireType("TheBazaar.DataExtensions");
            MethodInfo replay = null;
            foreach (MethodInfo method in extensions.GetMethods(
                BindingFlags.Static |
                BindingFlags.Public |
                BindingFlags.NonPublic))
            {
                if (method.Name == "IsReplayActive" &&
                    method.GetParameters().Length == 1)
                {
                    replay = method;
                    break;
                }
            }
            if (current == null || replay == null)
            {
                throw new MissingMethodException(
                    "Replay-state guard is unavailable.");
            }
            return Convert.ToBoolean(
                replay.Invoke(null, new[] { current.GetValue(null, null) }),
                CultureInfo.InvariantCulture);
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
            return property == null
                ? null
                : property.GetValue(value, null);
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
    }
}
