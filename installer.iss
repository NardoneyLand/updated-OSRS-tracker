; installer.iss — Inno Setup script for the Windows installer.
;
; Build (after .\build.ps1):
;     iscc /DAppVersion=1.0.0 installer.iss
; Output: dist\OSRS-Tracker-Setup-<version>.exe
;
; Installs per-user (no admin prompt) with a Start Menu entry, optional desktop
; shortcut, and an uninstaller. App data in %APPDATA%\OSRS Tracker is kept on
; uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Fixed AppId: a newer installer upgrades the existing install in place
; (same folder, data in %APPDATA% untouched).
AppId={{6C1F0E64-5B1B-4C55-9B7E-0A5A1D3E7F21}
; Close a running OSRS Tracker before replacing it (avoids "file in use").
CloseApplications=force
RestartApplications=no
AppName=OSRS Tracker
AppVersion={#AppVersion}
AppPublisher=NardoneyLand
AppPublisherURL=https://github.com/NardoneyLand/updated-OSRS-tracker
DefaultDirName={autopf}\OSRS Tracker
DefaultGroupName=OSRS Tracker
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=OSRS-Tracker-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\OSRS Tracker.exe
#ifexist "icon.ico"
SetupIconFile=icon.ico
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\OSRS Tracker.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\OSRS Tracker"; Filename: "{app}\OSRS Tracker.exe"
Name: "{group}\Uninstall OSRS Tracker"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OSRS Tracker"; Filename: "{app}\OSRS Tracker.exe"; Tasks: desktopicon

[Run]
; Not skipped in /SILENT mode, so "Check for updates" reopens the app after
; updating. (Interactive installs still show this as a checkbox.)
Filename: "{app}\OSRS Tracker.exe"; Description: "Launch OSRS Tracker"; Flags: nowait postinstall

[Code]
// The app is a PyInstaller one-file exe. When "Check for updates" starts
// this installer, the installer inherits the old app's PyInstaller
// variables and would pass them on to the new version launched above,
// which then fails with "Failed to load Python DLL" (they point at the old
// app's deleted unpack folder). This makes the new version start fresh.
// The app also clears them itself from 1.0.2; this covers older versions.
function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
begin
  SetEnvironmentVariable('PYINSTALLER_RESET_ENVIRONMENT', '1');
  Result := True;
end;
