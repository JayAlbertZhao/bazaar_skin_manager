#ifndef MyAppVersion
  #define MyAppVersion "0.9.4"
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

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--restore-before-uninstall"; StatusMsg: "Restoring manager-owned game files..."; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RestoreGameFiles"

[Code]
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
