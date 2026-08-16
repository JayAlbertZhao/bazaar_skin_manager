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
        private static readonly bool ForceLocalPvpResultVoiceForValidation =
            false;

        private static AudioSource _source;
        private static object _voiceOwner;
        private static bool _voicePlayback;
        private static float _currentPackGain;
        private static MethodInfo _getPreferencesData;
        private static PropertyInfo _volumeMaster;
        private static PropertyInfo _volumeVoiceover;
        private static bool _volumeReadFailureReported;
        private static bool _installed;
        private static PvpResultVoiceSide _pvpResultVoiceSide;
        private static readonly System.Random PvpVoiceRandom =
            new System.Random();
        private static readonly object PvpVoiceRandomLock = new object();
        private static readonly HashSet<string> ReportedRoutes =
            new HashSet<string>(StringComparer.Ordinal);

        private enum PvpResultVoiceSide
        {
            None,
            Opponent,
            Player
        }

        private sealed class PvpResultVoiceState
        {
            internal object CardAudio;
            internal FieldInfo NonVerbalField;
            internal bool OriginalNonVerbal;
            internal bool Changed;
            internal bool ResetChoice;
        }

        [ThreadStatic]
        private static int _characterSelectContext;

        [ThreadStatic]
        private static RuntimePack _characterSelectPack;

        [ThreadStatic]
        private static int _equipMusicContext;

        [ThreadStatic]
        private static RuntimePack _equipMusicPack;

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
                !HasAnyAudioPack())
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
                Type soundEventListener =
                    RequireType("TheBazaar.SoundEventListener");
                MethodInfo finishCombat = AccessTools.Method(
                    soundEventListener,
                    "OnFinishCombat");
                Type soundManager = RequireType("SoundManager");
                MethodInfo setVolume = FindSetVolume(soundManager);
                BindGameVoiceVolume();
                if (playVo == null ||
                    state == null ||
                    setHero == null ||
                    equip == null ||
                    playSfx == null ||
                    finishCombat == null ||
                    setVolume == null)
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
                harmony.Patch(
                    finishCombat,
                    prefix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(BeginExperimentalPvpResultVoice)),
                    postfix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(EndExperimentalPvpResultVoice)),
                    finalizer: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(FinalizeExperimentalPvpResultVoice)));
                harmony.Patch(
                    setVolume,
                    postfix: new HarmonyMethod(
                        typeof(RuntimeAudioReplacement),
                        nameof(RefreshExternalVoiceVolume)));
                _installed = true;
                Plugin.Log.LogInfo(
                    "External voice replacement enabled for exact per-hero, " +
                    "merchant, and menu routes; playback follows the game's " +
                    "Master and Voiceover volume controls.");
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
            _currentPackGain = 0f;
            _getPreferencesData = null;
            _volumeMaster = null;
            _volumeVoiceover = null;
            _volumeReadFailureReported = false;
            _installed = false;
            _pvpResultVoiceSide = PvpResultVoiceSide.None;
            ReportedRoutes.Clear();
            _characterSelectContext = 0;
            _characterSelectPack = null;
            _equipMusicContext = 0;
            _equipMusicPack = null;
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
                if (!isHero &&
                    hookType == 10 &&
                    IsCurrentRunState("PVPCombat") &&
                    IsTargetHeroSelected())
                {
                    PvpResultVoiceSide side =
                        ChooseExperimentalPvpResultVoice();
                    if (side == PvpResultVoiceSide.Player)
                    {
                        // The native call at combatant death is the
                        // opponent's result line. Suppress it only when this
                        // combat chose the local player; OnFinishCombat will
                        // request the local result through the game's normal
                        // hero route.
                        return false;
                    }
                    return true;
                }
                object cardAudio = ResolveSelectedCardAudio(
                    isHero,
                    hookType);
                UnityEngine.Object unityCardAudio =
                    cardAudio as UnityEngine.Object;
                if (unityCardAudio == null)
                {
                    return true;
                }
                RuntimePack pack = isHero
                    ? PackForAudioObject(unityCardAudio.name)
                    : SelectedHeroPack();
                if (pack == null || pack.Audio == null)
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
                if (!pack.Audio.TryRoute(
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
                // validated builds 24001960 and 24570932. If an unknown state
                // cannot be proven safe,
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

                if (!Play(pack, route, __instance, true))
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

        private static PvpResultVoiceSide ChooseExperimentalPvpResultVoice()
        {
            if (_pvpResultVoiceSide != PvpResultVoiceSide.None)
            {
                return _pvpResultVoiceSide;
            }

            object opponentAudio = ResolveSelectedCardAudio(false, 10);
            object nonVerbal = ReadMember(opponentAudio, "NonVerbal");
            if (opponentAudio == null || nonVerbal == null)
            {
                // Unknown runtime state must preserve the native call. The
                // experiment is never allowed to turn a bind failure into a
                // guaranteed silent result.
                _pvpResultVoiceSide = PvpResultVoiceSide.Opponent;
            }
            else if (Convert.ToBoolean(
                nonVerbal,
                CultureInfo.InvariantCulture))
            {
                _pvpResultVoiceSide = PvpResultVoiceSide.Player;
            }
            else
            {
                // The validation switch can force the local-player branch
                // without perturbing the game's RNG. Public builds keep the
                // independent 50/50 System.Random choice below.
                if (ForceLocalPvpResultVoiceForValidation)
                {
                    _pvpResultVoiceSide = PvpResultVoiceSide.Player;
                }
                else
                {
                    lock (PvpVoiceRandomLock)
                    {
                        // Do not advance UnityEngine.Random: voice selection
                        // must not perturb game/presentation RNG sequences.
                        _pvpResultVoiceSide =
                            PvpVoiceRandom.Next(0, 2) == 0
                                ? PvpResultVoiceSide.Opponent
                                : PvpResultVoiceSide.Player;
                    }
                }
            }
            Plugin.Log.LogInfo(
                "Experimental PvP result voice selected: " +
                _pvpResultVoiceSide.ToString().ToLowerInvariant() + ".");
            return _pvpResultVoiceSide;
        }

        private static void BeginExperimentalPvpResultVoice(
            object __instance,
            ref PvpResultVoiceState __state)
        {
            __state = null;
            try
            {
                string playerResult = Convert.ToString(
                    ReadMember(
                        __instance,
                        "ParameterPlayerVictoryDefeat"),
                    CultureInfo.InvariantCulture);
                if (string.IsNullOrEmpty(playerResult) ||
                    !IsCurrentRunState("PVPCombat") ||
                    !IsTargetHeroSelected())
                {
                    return;
                }

                __state = new PvpResultVoiceState
                {
                    ResetChoice = true
                };
                if (ChooseExperimentalPvpResultVoice() !=
                    PvpResultVoiceSide.Player)
                {
                    return;
                }

                object soundManager = ReadMember(
                    __instance,
                    "_soundManager");
                object cardAudioHandler = ReadMember(
                    soundManager,
                    "CardAudioHandler");
                object activeCardAudio = ReadMember(
                    cardAudioHandler,
                    "ActiveCardAudio");
                if (activeCardAudio == null)
                {
                    Plugin.Log.LogWarning(
                        "Experimental player PvP result voice could not " +
                        "bind the opponent CardAudio; native behavior was " +
                        "left unchanged.");
                    return;
                }

                FieldInfo nonVerbalField = AccessTools.Field(
                    activeCardAudio.GetType(),
                    "NonVerbal");
                if (nonVerbalField == null)
                {
                    Plugin.Log.LogWarning(
                        "Experimental player PvP result voice could not " +
                        "bind CardAudio.NonVerbal.");
                    return;
                }

                bool originalNonVerbal = Convert.ToBoolean(
                    nonVerbalField.GetValue(activeCardAudio),
                    CultureInfo.InvariantCulture);
                __state.CardAudio = activeCardAudio;
                __state.NonVerbalField = nonVerbalField;
                __state.OriginalNonVerbal = originalNonVerbal;
                __state.Changed = !originalNonVerbal;
                if (__state.Changed)
                {
                    nonVerbalField.SetValue(activeCardAudio, true);
                }
                Plugin.Log.LogInfo(
                    "Experimental local hero PvP result requested: " +
                    playerResult + ".");
            }
            catch (Exception exception)
            {
                RestoreExperimentalPvpResultVoice(__state);
                _pvpResultVoiceSide = PvpResultVoiceSide.None;
                __state = null;
                Plugin.Log.LogWarning(
                    "Experimental PvP result selection failed open: " +
                    exception.Message);
            }
        }

        private static void EndExperimentalPvpResultVoice(
            PvpResultVoiceState __state)
        {
            RestoreExperimentalPvpResultVoice(__state);
        }

        private static Exception FinalizeExperimentalPvpResultVoice(
            Exception __exception,
            PvpResultVoiceState __state)
        {
            RestoreExperimentalPvpResultVoice(__state);
            return __exception;
        }

        private static void RestoreExperimentalPvpResultVoice(
            PvpResultVoiceState state)
        {
            if (state != null &&
                state.Changed &&
                state.CardAudio != null &&
                state.NonVerbalField != null)
            {
                state.NonVerbalField.SetValue(
                    state.CardAudio,
                    state.OriginalNonVerbal);
                state.Changed = false;
            }
            if (state != null && state.ResetChoice)
            {
                _pvpResultVoiceSide = PvpResultVoiceSide.None;
                state.ResetChoice = false;
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
            string hero = __args != null && __args.Length == 1
                ? Convert.ToString(
                    __args[0],
                    CultureInfo.InvariantCulture)
                : string.Empty;
            RuntimePack pack = Plugin.PackForHero(
                NormalizeAudioHeroName(hero));
            __state = pack != null && pack.Audio != null;
            if (__state)
            {
                _characterSelectPack = pack;
                _characterSelectContext++;
            }
        }

        private static void EndCharacterSelect(bool __state)
        {
            if (__state && _characterSelectContext > 0)
            {
                _characterSelectContext--;
                if (_characterSelectContext == 0)
                {
                    _characterSelectPack = null;
                }
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
            RuntimePack pack = Plugin.PackForHero(
                NormalizeAudioHeroName(
                    Convert.ToString(hero, CultureInfo.InvariantCulture)));
            __state = pack != null && pack.Audio != null;
            if (__state)
            {
                _equipMusicPack = pack;
                _equipMusicContext++;
            }
        }

        private static void EndEquipMusic(bool __state)
        {
            if (__state && _equipMusicContext > 0)
            {
                _equipMusicContext--;
                if (_equipMusicContext == 0)
                {
                    _equipMusicPack = null;
                }
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
                RuntimePack pack = _characterSelectContext > 0
                    ? _characterSelectPack
                    : _equipMusicPack;
                if (pack == null || pack.Audio == null ||
                    !pack.Audio.TryRoute(
                        CanonicalGuid(guid),
                        new List<AudioSelector>(),
                        out route) || route.Category != "menu_voice")
                {
                    return true;
                }
                bool contextMatches =
                    (_characterSelectContext > 0 &&
                        route.LogicalSlot == "Menu.CharacterSelect") ||
                    (_equipMusicContext > 0 &&
                        route.LogicalSlot == "Menu.EquipMusic");
                if (!contextMatches || !Play(pack, route, null, false))
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
            RuntimePack pack,
            LoadedAudioRoute route,
            object voiceOwner,
            bool voicePlayback)
        {
            if (_source == null ||
                route == null || pack == null || pack.Audio == null)
            {
                return false;
            }
            LoadedAudioVariant variant =
                pack.Audio.Choose(route);
            if (variant == null || variant.Clip == null)
            {
                return false;
            }

            _source.Stop();
            _source.clip = variant.Clip;
            if (!TryApplyGameVoiceVolume(pack.Audio.Gain))
            {
                _source.clip = null;
                return false;
            }
            _currentPackGain = pack.Audio.Gain;
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

        private static void BindGameVoiceVolume()
        {
            Type playerPreferences = RequireType("PlayerPreferences");
            PropertyInfo data = AccessTools.Property(
                playerPreferences,
                "Data");
            if (data == null ||
                data.GetGetMethod(true) == null ||
                !data.GetGetMethod(true).IsStatic)
            {
                throw new MissingMemberException(
                    "PlayerPreferences.Data is not an exact readable static property.");
            }

            PropertyInfo master = AccessTools.Property(
                data.PropertyType,
                "VolumeMaster");
            PropertyInfo voiceover = AccessTools.Property(
                data.PropertyType,
                "VolumeVoiceover");
            if (!IsReadableFloatProperty(master) ||
                !IsReadableFloatProperty(voiceover))
            {
                throw new MissingMemberException(
                    "The exact Master/Voiceover preference properties are absent.");
            }

            _getPreferencesData = data.GetGetMethod(true);
            _volumeMaster = master;
            _volumeVoiceover = voiceover;
        }

        private static bool IsReadableFloatProperty(PropertyInfo property)
        {
            return property != null &&
                property.PropertyType == typeof(float) &&
                property.GetGetMethod(true) != null;
        }

        private static MethodInfo FindSetVolume(Type soundManager)
        {
            foreach (MethodInfo candidate in soundManager.GetMethods(
                BindingFlags.Static |
                BindingFlags.Public |
                BindingFlags.NonPublic))
            {
                ParameterInfo[] parameters = candidate.GetParameters();
                if (candidate.Name == "SetVolume" &&
                    candidate.ReturnType == typeof(void) &&
                    parameters.Length == 2 &&
                    parameters[0].ParameterType.FullName ==
                        "SoundManager+VolumeType" &&
                    parameters[1].ParameterType == typeof(float))
                {
                    return candidate;
                }
            }
            return null;
        }

        private static void RefreshExternalVoiceVolume()
        {
            if (_source == null || !_source.isPlaying)
            {
                return;
            }
            TryApplyGameVoiceVolume(_currentPackGain);
        }

        private static bool TryApplyGameVoiceVolume(float packGain)
        {
            try
            {
                if (_source == null ||
                    _getPreferencesData == null ||
                    _volumeMaster == null ||
                    _volumeVoiceover == null)
                {
                    return false;
                }
                object preferences = _getPreferencesData.Invoke(null, null);
                if (preferences == null)
                {
                    return false;
                }
                float master = Convert.ToSingle(
                    _volumeMaster.GetValue(preferences, null),
                    CultureInfo.InvariantCulture);
                float voiceover = Convert.ToSingle(
                    _volumeVoiceover.GetValue(preferences, null),
                    CultureInfo.InvariantCulture);
                _source.volume = packGain *
                    Mathf.Clamp01(master) *
                    Mathf.Clamp01(voiceover);
                return true;
            }
            catch (Exception exception)
            {
                if (!_volumeReadFailureReported)
                {
                    _volumeReadFailureReported = true;
                    Plugin.Log.LogWarning(
                        "External voice volume could not follow the game's " +
                        "Master/Voiceover controls; new replacements will " +
                        "fail open to FMOD: " + exception.Message);
                }
                return false;
            }
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

        private static bool IsCurrentRunState(string expected)
        {
            Type data = RequireType("TheBazaar.Data");
            PropertyInfo current = data.GetProperty(
                "CurrentState",
                BindingFlags.Static |
                BindingFlags.Public |
                BindingFlags.NonPublic);
            if (current == null)
            {
                throw new MissingMemberException(
                    data.FullName,
                    "CurrentState");
            }
            object runState = current.GetValue(null, null);
            object stateName = ReadMember(runState, "StateName");
            return stateName != null && string.Equals(
                Convert.ToString(
                    stateName,
                    CultureInfo.InvariantCulture),
                expected,
                StringComparison.Ordinal);
        }

        private static bool IsTargetHeroSelected()
        {
            return SelectedHeroPack() != null;
        }

        private static bool HasAnyAudioPack()
        {
            foreach (RuntimePack pack in Plugin.ActivePacks)
            {
                if (pack.Audio != null)
                {
                    return true;
                }
            }
            return false;
        }

        private static RuntimePack SelectedHeroPack()
        {
            UnityEngine.Object heroAudio =
                ResolveSelectedCardAudio(true, 10) as UnityEngine.Object;
            return heroAudio == null
                ? null
                : PackForAudioObject(heroAudio.name);
        }

        private static RuntimePack PackForAudioObject(string audioName)
        {
            if (string.IsNullOrEmpty(audioName))
            {
                return null;
            }
            const string suffix = "AudioSO";
            string hero = audioName.EndsWith(
                suffix,
                StringComparison.OrdinalIgnoreCase)
                ? audioName.Substring(0, audioName.Length - suffix.Length)
                : audioName;
            if (hero.EndsWith("Merchant", StringComparison.OrdinalIgnoreCase))
            {
                hero = hero.Substring(0, hero.Length - "Merchant".Length);
            }
            return Plugin.PackForHero(NormalizeAudioHeroName(hero));
        }

        private static string NormalizeAudioHeroName(string hero)
        {
            if (string.Equals(hero, "Pyg", StringComparison.OrdinalIgnoreCase))
            {
                return "Pygmalien";
            }
            if (string.Equals(
                hero,
                "TheDragons",
                StringComparison.OrdinalIgnoreCase))
            {
                return "Hero8";
            }
            return hero;
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
