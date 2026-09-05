; Inno Setup script — builds the OpenMBB Windows installer.
;
;   iscc /DAppVersion=0.28.0 /DDistDir=C:\path\to\dist\onedir\openmbb packaging\openmbb.iss
;
; DistDir is the PyInstaller --onedir tree (python packaging/build.py --mode onedir):
; openmbb.exe beside an _internal\ directory. The installer ships that tree, NOT
; the portable single-file exe. A onefile build unpacks itself into %TEMP% at
; every launch, which is the single biggest reason an unsigned PyInstaller build
; trips heuristic antivirus (Defender's Wacatac!ml class); installed as a tree,
; the exe runs in place and nothing is extracted.
;
; CI (.github/workflows/build.yml) builds and verifies the tree, then runs this;
; the installer is attached to the release beside the portable exe. Installs
; per-user (no admin): Start menu entry, optional desktop icon, uninstaller in
; Windows Settings.

; x64compatible (below) needs Inno Setup 6.3+; fail loud on older compilers.
#if Ver < EncodeVer(6,3,0)
  #error Inno Setup 6.3 or newer is required to compile this script
#endif

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir AddBackslash(SourcePath) + "..\dist\onedir\openmbb"
#endif

; Fail at compile time, not with an installer that ships the wrong thing.
#if !FileExists(DistDir + "\openmbb.exe")
  #error DistDir has no openmbb.exe - run: python packaging/build.py --mode onedir
#endif
#if !DirExists(DistDir + "\_internal")
  #error DistDir has no _internal\ - that is a onefile exe, not the onedir tree this installer ships
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
; The whole --onedir tree: openmbb.exe and everything under _internal\.
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Deliberately NO [InstallDelete] of {app}\_internal. It would run before the
; file copy, and Inno keeps no backup of what it deletes: a user who declines
; to close a running OpenMBB would get every unlocked file under _internal\
; wiped, then an in-use abort on python3xx.dll, and be left with a tree that no
; longer starts. A file an older build shipped and a newer one does not is dead
; weight, not a hazard (the frozen app imports from its own archive first), and
; the uninstaller removes every version's files because Inno appends to the
; existing uninstall log on upgrade.

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
