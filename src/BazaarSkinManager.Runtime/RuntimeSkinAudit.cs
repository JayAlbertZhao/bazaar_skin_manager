using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Threading;
using System.Threading.Tasks;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    [DataContract]
    internal sealed class SkinAuditRequest
    {
        [DataMember(Name = "enabled")]
        public bool Enabled = true;

        [DataMember(Name = "invoke_safe_loaders")]
        public bool InvokeSafeLoaders;

        [DataMember(Name = "exercise_central_standing")]
        public bool ExerciseCentralStanding;

        [DataMember(Name = "test_callbacks")]
        public bool TestCallbacks;
    }

    [DataContract]
    internal sealed class SkinReferenceMeasurement
    {
        [DataMember(Name = "field")]
        public string Field;

        [DataMember(Name = "field_type")]
        public string FieldType;

        [DataMember(Name = "runtime_key")]
        public string RuntimeKey;

        [DataMember(Name = "asset_guid")]
        public string AssetGuid;

        [DataMember(Name = "runtime_key_valid")]
        public bool RuntimeKeyValid;
    }

    [DataContract]
    internal sealed class SkinObjectMeasurement
    {
        [DataMember(Name = "source")]
        public string Source;

        [DataMember(Name = "type")]
        public string Type;

        [DataMember(Name = "name")]
        public string Name;

        [DataMember(Name = "dimensions")]
        public int[] Dimensions;

        [DataMember(Name = "hierarchy")]
        public string[] Hierarchy;
    }

    [DataContract]
    internal sealed class SkinLoaderMeasurement
    {
        [DataMember(Name = "loader")]
        public string Loader;

        [DataMember(Name = "status")]
        public string Status;

        [DataMember(Name = "detail")]
        public string Detail;

        [DataMember(Name = "result")]
        public SkinObjectMeasurement Result;
    }

    [DataContract]
    internal sealed class SkinAuditReport
    {
        [DataMember(Name = "schema_version")]
        public int SchemaVersion = 1;

        [DataMember(Name = "steam_build")]
        public string SteamBuild = "24001960";

        [DataMember(Name = "target_skin")]
        public string TargetSkin;

        [DataMember(Name = "references")]
        public List<SkinReferenceMeasurement> References =
            new List<SkinReferenceMeasurement>();

        [DataMember(Name = "loader_outcomes")]
        public List<SkinLoaderMeasurement> LoaderOutcomes =
            new List<SkinLoaderMeasurement>();

        [DataMember(Name = "loaded_objects")]
        public List<SkinObjectMeasurement> LoadedObjects =
            new List<SkinObjectMeasurement>();
    }

    internal static class RuntimeSkinAudit
    {
        private static readonly string AuditRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BazaarSkinManager",
            "TheBazaar",
            "skin-audit");
        private static readonly string RequestPath =
            Path.Combine(AuditRoot, "request.json");
        private static readonly string ResultRoot =
            Path.Combine(AuditRoot, "results");
        private static readonly string ReportPath =
            Path.Combine(ResultRoot, "skin-audit.json");

        private static readonly string[] AuditedLoaders =
        {
            "LoadDailyWeeklyImageAssetAsync",
            "LoadChestRewardAsync",
            "LoadCollectionDetailsAssetAsync",
            "LoadSkinEditSkinAsync",
            "LoadCollectionListAssetAsync",
            "LoadGameplayAssetAsync",
            "LoadMarketplaceDetailsAssetAsync",
            "LoadMarketplaceListAssetAsync",
            "LoadPortrait",
            "LoadPortraitSpriteAsync",
            "LoadAnimatedPortraitAsync",
            "LoadCollectibleInspectionAssetAsync",
            "LoadStoreImageAsync",
            "GenerateEncounterData"
        };

        private static readonly SkinAuditReport Report =
            new SkinAuditReport();
        private static readonly HashSet<string> OutcomeKeys =
            new HashSet<string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ObjectKeys =
            new HashSet<string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ObservedLoaders =
            new HashSet<string>(StringComparer.Ordinal);

        private static bool _requestChecked;
        private static bool _enabled;
        private static bool _invokeSafeLoaders;
        private static bool _exerciseCentralStanding;
        private static bool _testCallbacks;
        private static bool _fieldsCaptured;

        public static bool Enabled
        {
            get
            {
                EnsureRequest();
                return _enabled;
            }
        }

        public static bool InvokeSafeLoaders
        {
            get
            {
                EnsureRequest();
                return _invokeSafeLoaders;
            }
        }

        public static bool ExerciseCentralStanding
        {
            get
            {
                EnsureRequest();
                return _exerciseCentralStanding;
            }
        }

        public static bool TestCallbacks
        {
            get
            {
                EnsureRequest();
                return _testCallbacks;
            }
        }

        public static void CaptureTarget(object target)
        {
            if (!Enabled || target == null)
            {
                return;
            }

            UnityEngine.Object unityTarget = target as UnityEngine.Object;
            if (unityTarget == null)
            {
                return;
            }

            if (!_fieldsCaptured)
            {
                _fieldsCaptured = true;
                Report.TargetSkin = unityTarget.name;
                foreach (FieldInfo field in target.GetType().GetFields(
                    BindingFlags.Instance |
                    BindingFlags.Public |
                    BindingFlags.NonPublic))
                {
                    if (field.FieldType.FullName == null ||
                        field.FieldType.FullName.IndexOf(
                            "AssetReference",
                            StringComparison.Ordinal) < 0)
                    {
                        continue;
                    }

                    object reference = field.GetValue(target);
                    Report.References.Add(MeasureReference(field, reference));
                }
                Report.References.Sort(
                    (left, right) => string.CompareOrdinal(
                        left.Field,
                        right.Field));
            }

            CaptureLoadedTargetObjects();
            WriteReport();
        }

        public static void RecordLoader(
            string loader,
            string status,
            string detail,
            object result)
        {
            RuntimeDiagnostics.ReportLoaderState(loader, status, detail);
            if (!Enabled)
            {
                return;
            }

            lock (Report)
            {
                ObservedLoaders.Add(loader);
                string key = loader + "|" + status + "|" + detail;
                if (OutcomeKeys.Add(key))
                {
                    SkinObjectMeasurement measured =
                        MeasureObject("loader:" + loader, result);
                    Report.LoaderOutcomes.Add(new SkinLoaderMeasurement
                    {
                        Loader = loader,
                        Status = status,
                        Detail = detail,
                        Result = measured
                    });
                    if (measured != null)
                    {
                        AddObject(measured);
                    }
                }
                WriteReport();
            }
        }

        public static void RecordMissingLoaders()
        {
            if (!Enabled)
            {
                return;
            }

            foreach (string loader in AuditedLoaders)
            {
                if (!ObservedLoaders.Contains(loader))
                {
                    string status = loader == "LoadGameplayAssetAsync"
                        ? "unsupported type"
                        : loader == "LoadSkinEditSkinAsync"
                            ? "wrong hero/skin"
                            : "not loaded";
                    string detail = loader == "LoadGameplayAssetAsync"
                        ? "The game implementation explicitly returns null for direct gameplay loading."
                        : loader == "LoadSkinEditSkinAsync"
                            ? "Skin-edit placement is outside the Mak default-skin target."
                            : "No call observed in the current requested runtime audit.";
                    RecordLoader(
                        loader,
                        status,
                        detail,
                        null);
                }
            }
        }

        private static SkinReferenceMeasurement MeasureReference(
            FieldInfo field,
            object reference)
        {
            string runtimeKey = "<null>";
            string assetGuid = string.Empty;
            bool valid = false;
            if (reference != null)
            {
                PropertyInfo keyProperty = AccessTools.Property(
                    reference.GetType(),
                    "RuntimeKey");
                object key = keyProperty == null
                    ? null
                    : keyProperty.GetValue(reference, null);
                runtimeKey = key == null ? "<null>" : key.ToString();

                PropertyInfo guidProperty = AccessTools.Property(
                    reference.GetType(),
                    "AssetGUID");
                object guid = guidProperty == null
                    ? null
                    : guidProperty.GetValue(reference, null);
                assetGuid = guid == null ? string.Empty : guid.ToString();

                MethodInfo validMethod = AccessTools.Method(
                    reference.GetType(),
                    "RuntimeKeyIsValid",
                    Type.EmptyTypes);
                if (validMethod != null)
                {
                    object value = validMethod.Invoke(reference, null);
                    valid = value is bool && (bool)value;
                }
            }

            return new SkinReferenceMeasurement
            {
                Field = field.Name,
                FieldType = field.FieldType.FullName,
                RuntimeKey = runtimeKey,
                AssetGuid = assetGuid,
                RuntimeKeyValid = valid
            };
        }

        private static void CaptureLoadedTargetObjects()
        {
            foreach (UnityEngine.Object value in
                Resources.FindObjectsOfTypeAll<UnityEngine.Object>())
            {
                if (value == null || string.IsNullOrEmpty(value.name))
                {
                    continue;
                }
                if (value.name.IndexOf(
                    "MAK_01a",
                    StringComparison.OrdinalIgnoreCase) < 0 &&
                    !string.Equals(
                        value.name,
                        "HeroItemView_MAK",
                        StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                SkinObjectMeasurement measured =
                    MeasureObject("loaded-resource", value);
                if (measured != null)
                {
                    AddObject(measured);
                }
            }
        }

        private static void AddObject(SkinObjectMeasurement measured)
        {
            string firstPath = measured.Hierarchy == null ||
                measured.Hierarchy.Length == 0
                ? string.Empty
                : measured.Hierarchy[0];
            string key = measured.Source + "|" + measured.Type + "|" +
                measured.Name + "|" + firstPath;
            if (ObjectKeys.Add(key))
            {
                Report.LoadedObjects.Add(measured);
            }
        }

        private static SkinObjectMeasurement MeasureObject(
            string source,
            object result)
        {
            if (result == null)
            {
                return null;
            }

            Type type = result.GetType();
            UnityEngine.Object unityObject = result as UnityEngine.Object;
            var measurement = new SkinObjectMeasurement
            {
                Source = source,
                Type = type.FullName,
                Name = unityObject == null
                    ? type.Name
                    : unityObject.name,
                Dimensions = Dimensions(unityObject),
                Hierarchy = DescribeObject(result)
            };
            return measurement;
        }

        private static int[] Dimensions(UnityEngine.Object value)
        {
            Texture texture = value as Texture;
            if (texture != null)
            {
                return new[] { texture.width, texture.height };
            }

            Sprite sprite = value as Sprite;
            if (sprite != null)
            {
                return new[]
                {
                    Mathf.RoundToInt(sprite.rect.width),
                    Mathf.RoundToInt(sprite.rect.height)
                };
            }
            return new int[0];
        }

        private static string[] DescribeObject(object result)
        {
            GameObject root = result as GameObject;
            Component component = result as Component;
            if (root == null && component != null)
            {
                root = component.gameObject;
            }
            if (root != null)
            {
                return DescribeHierarchy(root.transform).ToArray();
            }

            var values = new List<string>();
            foreach (FieldInfo field in result.GetType().GetFields(
                BindingFlags.Instance |
                BindingFlags.Public |
                BindingFlags.NonPublic))
            {
                UnityEngine.Object nested =
                    field.GetValue(result) as UnityEngine.Object;
                if (nested == null)
                {
                    continue;
                }
                int[] dimensions = Dimensions(nested);
                string size = dimensions.Length == 2
                    ? dimensions[0] + "x" + dimensions[1]
                    : "-";
                values.Add(
                    field.Name + "|" + nested.GetType().FullName +
                    "|" + nested.name + "|" + size);
            }
            return values.ToArray();
        }

        private static IEnumerable<string> DescribeHierarchy(Transform root)
        {
            int count = 0;
            foreach (Transform transform in
                root.GetComponentsInChildren<Transform>(true))
            {
                if (count++ >= 256)
                {
                    yield return "<truncated>";
                    yield break;
                }

                string components = string.Join(
                    ",",
                    transform.GetComponents<Component>()
                        .Where(value => value != null)
                        .Select(value => value.GetType().FullName)
                        .ToArray());
                Image image = transform.GetComponent<Image>();
                RawImage rawImage = transform.GetComponent<RawImage>();
                SpriteRenderer spriteRenderer =
                    transform.GetComponent<SpriteRenderer>();
                string asset = image != null && image.sprite != null
                    ? image.sprite.name
                    : rawImage != null && rawImage.texture != null
                        ? rawImage.texture.name
                        : spriteRenderer != null &&
                          spriteRenderer.sprite != null
                            ? spriteRenderer.sprite.name
                            : "<none>";
                yield return RelativePath(root, transform) +
                    "|active=" + transform.gameObject.activeInHierarchy +
                    "|components=" + components +
                    "|asset=" + asset;
            }
        }

        private static string RelativePath(Transform root, Transform value)
        {
            if (root == value)
            {
                return root.name;
            }

            var names = new List<string>();
            Transform current = value;
            while (current != null && current != root)
            {
                names.Add(current.name);
                current = current.parent;
            }
            names.Add(root.name);
            names.Reverse();
            return string.Join("/", names.ToArray());
        }

        private static void EnsureRequest()
        {
            if (_requestChecked)
            {
                return;
            }

            _requestChecked = true;
            if (!File.Exists(RequestPath))
            {
                return;
            }

            var serializer =
                new DataContractJsonSerializer(typeof(SkinAuditRequest));
            using (FileStream stream = File.OpenRead(RequestPath))
            {
                SkinAuditRequest request =
                    (SkinAuditRequest)serializer.ReadObject(stream);
                _enabled = request == null || request.Enabled;
                _invokeSafeLoaders =
                    request != null && request.InvokeSafeLoaders;
                _exerciseCentralStanding =
                    request != null && request.ExerciseCentralStanding;
                _testCallbacks =
                    request != null && request.TestCallbacks;
            }
            if (_enabled)
            {
                Directory.CreateDirectory(ResultRoot);
                Plugin.Log.LogInfo(
                    "Runtime skin audit requested at " + RequestPath);
            }
        }

        private static void WriteReport()
        {
            if (!_enabled)
            {
                return;
            }

            Directory.CreateDirectory(ResultRoot);
            var serializer =
                new DataContractJsonSerializer(typeof(SkinAuditReport));
            using (FileStream stream = File.Create(ReportPath))
            {
                serializer.WriteObject(stream, Report);
            }
        }
    }

    internal sealed class RuntimeSkinAuditScanner : MonoBehaviour
    {
        private float _nextScan;
        private float _started;
        private object _target;
        private bool _requestedAuditStarted;
        private bool _requestedAuditFinished;
        private bool _missingRecorded;

        private void Awake()
        {
            _started = Time.unscaledTime;
        }

        private void Update()
        {
            if (!RuntimeSkinAudit.Enabled || Time.unscaledTime < _nextScan)
            {
                return;
            }

            _nextScan = Time.unscaledTime + 2f;
            Type skinType = SkinPatchTargets.SkinType();
            if (skinType != null)
            {
                foreach (UnityEngine.Object candidate in
                    Resources.FindObjectsOfTypeAll(skinType))
                {
                    if (SkinPatchTargets.ShouldReplace(candidate))
                    {
                        _target = candidate;
                        RuntimeSkinAudit.CaptureTarget(candidate);
                        break;
                    }
                }
            }

            if (!_requestedAuditStarted &&
                _target != null &&
                Time.unscaledTime - _started >= 4f)
            {
                _requestedAuditStarted = true;
                RunRequestedAudit(_target);
            }

            if (!_missingRecorded &&
                (_requestedAuditFinished ||
                 Time.unscaledTime - _started >= 45f))
            {
                _missingRecorded = true;
                RuntimeSkinAudit.RecordMissingLoaders();
            }
        }

        private async void RunRequestedAudit(object target)
        {
            try
            {
                if (RuntimeSkinAudit.TestCallbacks)
                {
                    string callbackDetail;
                    bool callbackOk =
                        SkinLoaderCoverage.RunRestorationSelfTest(
                            out callbackDetail);
                    RuntimeSkinAudit.RecordLoader(
                        "SkinLoaderAppliedMarker.Restore",
                        callbackOk ? "applied" : "unsupported type",
                        callbackDetail,
                        null);
                }

                if (RuntimeSkinAudit.InvokeSafeLoaders)
                {
                    await InvokeSafeLoaders(target);
                }

                if (RuntimeSkinAudit.ExerciseCentralStanding)
                {
                    bool completed = false;
                    string centralDetail = "No HeroSelectDisplay was reachable.";
                    for (int attempt = 0; attempt < 60 && !completed; attempt++)
                    {
                        completed =
                            HeroSelectStandingState.TryRunDiagnostic(
                                target,
                                out centralDetail);
                        if (!completed)
                        {
                            await Task.Delay(500);
                        }
                    }
                    string targetCode = Plugin.ActivePack == null
                        ? "UNKNOWN"
                        : Plugin.ActivePack.TargetHeroCode();
                    RuntimeSkinAudit.RecordLoader(
                        "HeroSelectDisplay." + targetCode + ".Diagnostic",
                        completed ? "applied" : "not loaded",
                        centralDetail,
                        null);
                }
            }
            catch (Exception exception)
            {
                RuntimeSkinAudit.RecordLoader(
                    "RequestedRuntimeAudit",
                    "not loaded",
                    exception.GetType().FullName + ": " + exception.Message,
                    null);
            }
            finally
            {
                _requestedAuditFinished = true;
            }
        }

        private static async Task InvokeSafeLoaders(object target)
        {
            await InvokeLoader(target, "LoadPortrait", new object[] { false });
            await InvokeLoader(target, "LoadPortrait", new object[] { true });
            await InvokeLoader(
                target,
                "LoadPortraitSpriteAsync",
                new object[] { CancellationToken.None });
            await InvokeLoader(
                target,
                "LoadDailyWeeklyImageAssetAsync",
                new object[0]);
            await InvokeLoader(
                target,
                "LoadCollectionListAssetAsync",
                new object[0]);
            await InvokeLoader(
                target,
                "LoadCollectionDetailsAssetAsync",
                new object[0]);
            await InvokeLoader(
                target,
                "LoadStoreImageAsync",
                new object[0]);
            await InvokeLoader(
                target,
                "LoadMarketplaceListAssetAsync",
                new object[0]);
            await InvokeLoader(
                target,
                "LoadMarketplaceDetailsAssetAsync",
                new object[0]);
            await InvokeLoader(
                target,
                "LoadAnimatedPortraitAsync",
                new object[] { CancellationToken.None });
            await InvokeLoader(
                target,
                "LoadCollectibleInspectionAssetAsync",
                new object[] { null, CancellationToken.None });
        }

        private static async Task InvokeLoader(
            object target,
            string loader,
            object[] arguments)
        {
            MethodInfo method = AccessTools.Method(
                target.GetType(),
                loader,
                arguments == null
                    ? Type.EmptyTypes
                    : arguments.Select(argument =>
                        argument == null
                            ? typeof(Camera)
                            : argument.GetType()).ToArray());
            if (method == null)
            {
                RuntimeSkinAudit.RecordLoader(
                    loader,
                    "unsupported type",
                    "Requested audit could not bind the exact loader signature.",
                    null);
                return;
            }

            object result = null;
            try
            {
                Task task;
                IDisposable ownershipScope =
                    loader == "LoadPortraitSpriteAsync"
                        ? VisualOwnership.AssumeLocalPortraitForDiagnostic()
                        : null;
                try
                {
                    task = method.Invoke(target, arguments) as Task;
                }
                finally
                {
                    if (ownershipScope != null)
                    {
                        ownershipScope.Dispose();
                    }
                }
                if (task == null)
                {
                    RuntimeSkinAudit.RecordLoader(
                        loader,
                        "not loaded",
                        "Requested loader did not return a Task.",
                        null);
                    return;
                }
                await task;
                PropertyInfo resultProperty =
                    task.GetType().GetProperty("Result");
                result = resultProperty == null
                    ? null
                    : resultProperty.GetValue(task, null);
                ValidateRequestedResult(loader, result);
            }
            catch (Exception exception)
            {
                Exception actual = exception is TargetInvocationException &&
                    exception.InnerException != null
                    ? exception.InnerException
                    : exception;
                RuntimeSkinAudit.RecordLoader(
                    loader,
                    "not loaded",
                    "Requested safe invocation failed: " +
                    actual.GetType().FullName + ": " + actual.Message,
                    null);
            }
            finally
            {
                CleanupInstantiatedResult(result);
            }
        }

        private static void ValidateRequestedResult(
            string loader,
            object result)
        {
            if (loader != "LoadCollectibleInspectionAssetAsync" ||
                result == null)
            {
                return;
            }

            FieldInfo backgroundField = AccessTools.Field(
                result.GetType(),
                "HeroSkinBackgroundImage");
            bool preloaded = Plugin.ActivePack.UsesPreloadedDeployment(
                "store_image");
            Texture2D expected = Plugin.ActivePack.Texture("store_image");
            bool replaced = backgroundField != null &&
                (preloaded
                    ? backgroundField.GetValue(result) is Texture2D
                    : object.ReferenceEquals(
                        backgroundField.GetValue(result),
                        expected));
            RuntimeSkinAudit.RecordLoader(
                loader + ".ValueTypeValidation",
                replaced ? "applied" : "not loaded",
                replaced
                    ? preloaded
                        ? "Returned CollectibleInspectorData retains the " +
                            "deploy-time patched background reference."
                        : "Returned CollectibleInspectorData carries the " +
                            "replacement background."
                    : preloaded
                        ? "Returned CollectibleInspectorData lost its " +
                            "deploy-time patched background reference."
                        : "Returned CollectibleInspectorData lost its " +
                            "mutated background.",
                result);
        }

        private static void CleanupInstantiatedResult(object result)
        {
            if (result == null)
            {
                return;
            }

            GameObject root = result as GameObject;
            if (root == null)
            {
                FieldInfo instanceField = AccessTools.Field(
                    result.GetType(),
                    "LoadedCollectibleInstance");
                root = instanceField == null
                    ? null
                    : instanceField.GetValue(result) as GameObject;
            }
            if (root != null)
            {
                StandingOverlay.RemoveFromWorld(root);
                UnityEngine.Object.Destroy(root);
            }
        }
    }
}
