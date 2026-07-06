; Inno Setup script — builds the OpenMBB Windows installer.
;
;   iscc /DAppVersion=0.9.0 /DExeSrc=..\dist\openmbb.exe packaging\openmbb.iss
;
; CI (.github/workflows/build.yml) runs this after the portable .exe is built
; and verified; both are attached to the release. Installs per-user (no admin):
; Start menu entry, optional desktop icon, uninstaller in Windows Settings.

; x64compatible (below) needs Inno Setup 6.3+; fail loud on older compilers.
#if Ver < EncodeVer(6,3,0)
  #error Inno Setup 6.3 or newer is required to compile this script
#endif

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef ExeSrc
  #define ExeSrc "..\dist\openmbb.exe"
#endif

[Setup]
; Never change AppId — it is how upgrades find the existing install.
AppId={{7E3C9D41-52A6-4F0B-B01D-3E8A2C64F9B7}
AppName=OpenMBB
AppVersion={#AppVersion}
AppPublisher=OpenMBB
AppPublisherURL=https://github.com/rodu4835/openmbb
AppSupportURL=https://github.com/rodu4835/openmbb/issues
DefaultDirName={autopf}\OpenMBB
DefaultGroupName=OpenMBB
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=openmbb-setup-windows-x64
SetupIconFile=icon\openmbb.ico
UninstallDisplayIcon={app}\openmbb.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#ExeSrc}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\OpenMBB"; Filename: "{app}\openmbb.exe"
Name: "{autodesktop}\OpenMBB"; Filename: "{app}\openmbb.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\openmbb.exe"; Description: "{cm:LaunchProgram,OpenMBB}"; Flags: nowait postinstall skipifsilent
