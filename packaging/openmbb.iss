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
Name: "{autoprograms}\OpenMBB"; Filename: "{app}\openmbb.exe"; WorkingDir: "{userdocs}"
Name: "{autodesktop}\OpenMBB"; Filename: "{app}\openmbb.exe"; WorkingDir: "{userdocs}"; Tasks: desktopicon

[Run]
Filename: "{app}\openmbb.exe"; WorkingDir: "{userdocs}"; Description: "{cm:LaunchProgram,OpenMBB}"; Flags: nowait postinstall skipifsilent

[Code]
{ G8: warn before letting an older setup silently downgrade a newer install }
function CompareVersion(A, B: String): Integer;
var
  PA, PB, NA, NB: Integer;
begin
  Result := 0;
  while ((A <> '') or (B <> '')) and (Result = 0) do
  begin
    PA := Pos('.', A); if PA = 0 then PA := Length(A) + 1;
    PB := Pos('.', B); if PB = 0 then PB := Length(B) + 1;
    NA := StrToIntDef(Copy(A, 1, PA - 1), 0);
    NB := StrToIntDef(Copy(B, 1, PB - 1), 0);
    if NA > NB then Result := 1
    else if NA < NB then Result := -1;
    A := Copy(A, PA + 1, Length(A));
    B := Copy(B, PB + 1, Length(B));
  end;
end;

function InitializeSetup(): Boolean;
var
  Installed: String;
begin
  Result := True;
  if RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7E3C9D41-52A6-4F0B-B01D-3E8A2C64F9B7}_is1',
      'DisplayVersion', Installed) then
  begin
    if (Installed <> '') and (CompareVersion(Installed, '{#AppVersion}') > 0) then
    begin
      if MsgBox('A newer OpenMBB (version ' + Installed + ') is already installed.' + #13#10 +
        'Install the older version {#AppVersion} anyway (downgrade)?',
        mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
    end;
  end;
end;
