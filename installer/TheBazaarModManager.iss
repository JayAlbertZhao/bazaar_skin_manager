#ifndef MyAppVersion
  #define MyAppVersion "1.4.6"
#endif
#ifndef SourceRoot
  #define SourceRoot ".."
#endif

#define MyAppName "The Bazaar Skin Manager"
#define MyAppPublisher "The Bazaar Skin Manager"
#define MyAppExeName "TheBazaarModManager.exe"
#define MyAppId "{{8AE8DDF3-4E85-4722-9282-881D6567A02A}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\TheBazaarModManager
DefaultGroupName={#MyAppName}
UsePreviousAppDir=yes
UsePreviousGroup=yes
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=TheBazaarModManager-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceRoot}\dist\manager\TheBazaarModManager.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\dist\manager\manager-build.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\docs\portable-quick-start.txt"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
Source: "{#SourceRoot}\third_party\BepInEx\LICENSE.txt"; DestDir: "{app}"; DestName: "BepInEx-LICENSE.txt"; Flags: ignoreversion
Source: "{#SourceRoot}\third_party\BepInEx\NOTICE.md"; DestDir: "{app}"; DestName: "BepInEx-NOTICE.md"; Flags: ignoreversion

[InstallDelete]
; v1.2.x and earlier installed these as separate helper applications. Their
; functionality is now integrated into the manager; remove upgrade leftovers.
Type: files; Name: "{app}\TheBazaarAssetGenerator.exe"
Type: files; Name: "{app}\asset-generator-build.json"
Type: files; Name: "{app}\TheBazaarSpineManager.exe"
Type: files; Name: "{app}\spine-manager-build.json"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--restore-before-uninstall"; StatusMsg: "Restoring manager-owned game files..."; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RestoreGameFiles"

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8AE8DDF3-4E85-4722-9282-881D6567A02A}_is1';

function InstalledVersion(var Version: String): Boolean;
begin
  Result := RegQueryStringValue(
    HKCU64, UninstallKey, 'DisplayVersion', Version);
  if not Result then
    Result := RegQueryStringValue(
      HKCU32, UninstallKey, 'DisplayVersion', Version);
end;

procedure InitializeWizard();
var
  PreviousVersion: String;
begin
  if InstalledVersion(PreviousVersion) then
    WizardForm.WelcomeLabel2.Caption :=
      'Version ' + PreviousVersion + ' is already installed.' + #13#10 + #13#10 +
      'Setup will upgrade it in place to version {#MyAppVersion}. ' +
      'Manager workspaces and deployed-mod state will be preserved.';
end;

function InitializeUninstall(): Boolean;
begin
  if UninstallSilent then
    Result := True
  else
    Result := MsgBox(
      'Close The Bazaar before continuing.' + #13#10 + #13#10 +
      'The uninstaller will restore game files managed by this application. ' +
      'Your authoring workspaces in Local AppData are retained.',
      mbConfirmation, MB_OKCANCEL) = IDOK;
end;
