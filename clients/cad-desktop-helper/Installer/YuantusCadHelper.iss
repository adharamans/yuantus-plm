; YuantusCadHelper.iss
; ---------------------------------------------------------------------------
; Per-user, no-admin Inno Setup installer for the CAD Desktop Helper Bridge
; R3.2 ship artifacts. Implements the contract in
;   docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_INSTALLER_20260524.md
; (taskbook #638 / 18b83d73).
;
; KEY INVARIANTS (pinned by Installer/verify_installer_static.py):
;   * PrivilegesRequired=lowest  -> per-user, no elevation, no HKLM.
;   * Every file lands under {userappdata}\... at the EXACT paths the
;     S1-S11 runtime spawns from. The helper must NOT be relocated, so
;     there is no user-chosen install directory (DisableDirPage=yes) and
;     no {app}/Program Files layout.
;   * Signing is owner-local: the [SignTools] entry is only emitted when
;     /DSignToolCmd=... is passed at compile time. CI compiles WITHOUT it
;     and produces an UNSIGNED installer (graceful skip).
;   * The installer NEVER creates the DPAPI token, audit.db,
;     install-id.json, or any helper-session-*.json -- those are
;     runtime-owned (S3/S6) and created on first helper launch.
;   * The helper is spawn-on-demand: NO Windows Service, NO auto-start.
;   * Uninstall is an allow-list of installer-laid subdirs; it never
;     blanket-deletes the {userappdata}\YuantusPLM root (which holds the
;     preserved user-data set).
; ---------------------------------------------------------------------------

#define MyAppName "Yuantus CAD Helper Bridge"
#define MyAppPublisher "Yuantus PLM"
#define MyAppVersion "0.1.0"

; Staging directory holding PRE-BUILT artifacts. It is populated by the
; build phase (dotnet publish + the CADDedup bundle MSBuild PostBuild),
; NOT by this script -- see Installer/pack.ps1 and §3.C / guard 10. This
; .iss invokes no compiler.
#ifndef StagingDir
  #define StagingDir "staging"
#endif

; CAD-startup stub fenced-region markers (guard 7). The block the
; installer appends to a per-user CAD startup file is delimited by these
; exact markers so repeat installs/repairs replace (not duplicate) it and
; uninstall removes exactly this region.
#define FenceBegin "; ==== BEGIN YUANTUS CAD HELPER (auto-generated; do not edit inside) ===="
#define FenceEnd   "; ==== END YUANTUS CAD HELPER ===="

[Setup]
AppId={{B7B6F6D2-1E3A-4C2C-9E11-7D9F1C0A5E11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user, no administrator privileges. This is the load-bearing flag.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
; The helper is spawned from a FIXED %APPDATA% path; the user may not
; relocate it. Pin the base dir to {userappdata}\YuantusPLM and hide the
; directory page so there is no relocatable DefaultDirName.
DefaultDirName={userappdata}\YuantusPLM
DisableDirPage=yes
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=YuantusCadHelperSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Owner-local signing only. When /DSignToolCmd=... is supplied at compile
; time the installer + its payload binaries are signed; otherwise (CI) the
; build proceeds UNSIGNED. See [SignTools] below.
#ifdef SignToolCmd
SignTool=yuantussign
SignedUninstaller=yes
#endif

#ifdef SignToolCmd
[SignTools]
; $f is the file Inno passes to the signing command. SignToolCmd is the
; full signtool invocation, owner-supplied at compile time, e.g.:
;   iscc /DSignToolCmd="signtool sign /fd sha256 /a /n MyOrg $f" YuantusCadHelper.iss
Name: "yuantussign"; Command: "{#SignToolCmd}"
#endif

[Tasks]
; Checked by default. Unchecking it (or compiling/installing with the
; documented --skip-cad-config intent) is the opt-out for operators who
; manage CAD startup files centrally. Guard 7 requires this gate.
; NOTE: this writes the Yuantus startup STUB to a stable per-user file;
; the CAD host loads it only once its Support File Search Path includes
; the cad-bridge folder (operator one-time step / future per-host
; Support-path enhancement). See DEV/Verification MD §4 item 4.
Name: "cadconfig"; Description: "Install the Yuantus Lisp startup stub (reference it from each CAD host's Support path)"; GroupDescription: "CAD integration:"

[Files]
; --- helper + detector -> {userappdata}\YuantusPLM\helper\ -----------------
; (the two net6.0 self-contained .exe + their first-party managed DLLs)
Source: "{#StagingDir}\helper\*"; DestDir: "{userappdata}\YuantusPLM\helper"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- bridge DLL + Lisp shell -> {userappdata}\YuantusPLM\cad-bridge\ --------
Source: "{#StagingDir}\cad-bridge\*"; DestDir: "{userappdata}\YuantusPLM\cad-bridge"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- existing AutoCAD plugin -> {userappdata}\Autodesk\ApplicationPlugins\ --
; Adopt/overwrite in place if a prior CADDedup.bundle exists (§3.C); no
; second copy is created because the destination is the canonical path.
Source: "{#StagingDir}\CADDedup.bundle\*"; DestDir: "{userappdata}\Autodesk\ApplicationPlugins\CADDedup.bundle"; Flags: ignoreversion recursesubdirs createallsubdirs

; NOTE: there is intentionally NO [Files] entry for local-helper-token.dat,
; audit.db, install-id.json, or helper-session-*.json (guard 6) -- those
; are runtime-owned and created on first helper launch.

; NOTE: there is intentionally NO [Run] entry that launches or auto-starts
; the helper, and NO service registration (guard 5) -- the helper is
; spawned on demand by the CAD-side caller and idle-exits after 30 min.

[UninstallDelete]
; Allow-list of installer-laid subdirs ONLY (guard 8). We never list the
; {userappdata}\YuantusPLM root, so local-helper-token.dat / audit.db /
; install-id.json / helper-session-*.json at the root are PRESERVED.
Type: filesandordirs; Name: "{userappdata}\YuantusPLM\helper"
Type: filesandordirs; Name: "{userappdata}\YuantusPLM\cad-bridge"
Type: filesandordirs; Name: "{userappdata}\Autodesk\ApplicationPlugins\CADDedup.bundle"

[Code]
{ ----------------------------------------------------------------------- }
{ Running-helper handling (taskbook §3.E): read pid + image_path from the }
{ S3 session file at the %APPDATA%\YuantusPLM root, confirm the image_path }
{ is the helper we manage, verify the pid is live, then prompt + taskkill  }
{ that pid. We never delete the session file itself -- S3 stale-clean owns }
{ it, and stale session files must not block install after a forced kill.  }
{ ----------------------------------------------------------------------- }

function RootDir(): String;
begin
  Result := ExpandConstant('{userappdata}\YuantusPLM');
end;

function ManagedHelperExe(): String;
begin
  Result := ExpandConstant('{userappdata}\YuantusPLM\helper\yuantus-cad-helper.exe');
end;

{ Extract the quoted string value of a JSON "<key>": "<value>" pair.
  Returns '' when absent. Targets the named field specifically (rather
  than matching a literal anywhere in the document). }
function JsonStr(const Content, Key: String): String;
var
  P, Q: Integer;
  Tok: String;
begin
  Result := '';
  Tok := '"' + Key + '"';
  P := Pos(Tok, Content);
  if P = 0 then exit;
  Q := P + Length(Tok);
  while (Q <= Length(Content)) and (Content[Q] <> ':') do Inc(Q);
  Inc(Q);
  while (Q <= Length(Content)) and (Content[Q] <> '"') do Inc(Q);
  Inc(Q);  { now at first char of the value }
  while (Q <= Length(Content)) and (Content[Q] <> '"') do
  begin
    Result := Result + Content[Q];
    Inc(Q);
  end;
end;

{ Extract the integer value of a JSON "<key>": <number> pair. Returns -1
  when absent. Deliberately narrow -- the session document is small and
  machine-written by HelperSessionDocument (HelperRuntime.cs:410-453). }
function JsonInt(const Content, Key: String): Integer;
var
  P, Q: Integer;
  Tok, Digits: String;
  C: Char;
begin
  Result := -1;
  Tok := '"' + Key + '"';
  P := Pos(Tok, Content);
  if P = 0 then exit;
  Q := P + Length(Tok);
  { skip past ':' and whitespace }
  while (Q <= Length(Content)) and (Content[Q] <> ':') do Inc(Q);
  Inc(Q);
  Digits := '';
  while Q <= Length(Content) do
  begin
    C := Content[Q];
    if (C >= '0') and (C <= '9') then
      Digits := Digits + C
    else if Digits <> '' then
      break
    else if (C <> ' ') and (C <> #9) then
      break;
    Inc(Q);
  end;
  if Digits <> '' then
    Result := StrToIntDef(Digits, -1);
end;

function IsManagedHelperPidLive(Pid: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  if Pid <= 0 then exit;

  { Validate against the LIVE process table. This avoids treating a stale
    helper-session-*.json as still-running after taskkill /F, while keeping
    the same image-name guard used for the actual kill command. }
  Exec(ExpandConstant('{sys}\cmd.exe'),
       '/C tasklist /FI "PID eq ' + IntToStr(Pid) + '" /FI "IMAGENAME eq yuantus-cad-helper.exe" /NH | find /I "yuantus-cad-helper.exe" >NUL',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := (ResultCode = 0);
end;

{ Find a running helper via any helper-session-*.json at the root whose
  image_path matches the helper we manage AND whose pid is live; return its
  pid, else -1. }
function FindRunningHelperPid(): Integer;
var
  FindRec: TFindRec;
  Content, ImagePath: String;
  Pid: Integer;
begin
  Result := -1;
  if FindFirst(RootDir() + '\helper-session-*.json', FindRec) then
  begin
    try
      repeat
        if LoadStringFromFile(RootDir() + '\' + FindRec.Name, Content) then
        begin
          { only act when this session file's image_path field EQUALS the
            helper exe we manage. JSON escapes path separators as "\\", so
            unescape before comparing (case-insensitive). This rejects a
            stale file for a helper installed at a different path. }
          ImagePath := JsonStr(Content, 'image_path');
          StringChangeEx(ImagePath, '\\', '\', True);
          if (ImagePath <> '') and (CompareText(ImagePath, ManagedHelperExe()) = 0) then
          begin
            Pid := JsonInt(Content, 'pid');
            if (Pid > 0) and IsManagedHelperPidLive(Pid) then
            begin
              Result := Pid;
              exit;
            end;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

{ Returns True when no managed helper is running afterward (either none was
  running, or it was stopped). Returns False when a managed helper is still
  running (operator declined, or the kill did not take). }
function TryStopRunningHelper(): Boolean;
var
  Pid, ResultCode: Integer;
begin
  Pid := FindRunningHelperPid();
  if Pid <= 0 then
  begin
    Result := True;
    exit;
  end;
  if MsgBox('The Yuantus CAD helper (pid ' + IntToStr(Pid) +
            ') is running and must stop before files can be replaced.' + #13#10 +
            'Stop it now?', mbConfirmation, MB_YESNO) <> IDYES then
  begin
    Result := False;
    exit;
  end;
  { Kill ONLY when BOTH the pid AND the image name match -- so a stale
    session file whose pid was reused by an unrelated process cannot be
    killed. taskkill applies the filters against the LIVE process. Never a
    blind image-name kill, and never delete the session file (S3 owns it). }
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/FI "PID eq ' + IntToStr(Pid) + '" /FI "IMAGENAME eq yuantus-cad-helper.exe" /F',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
  Result := not IsManagedHelperPidLive(Pid);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Abort the install if a managed helper is still running -- do NOT proceed
    to replace file-locked binaries. }
  if TryStopRunningHelper() then
    Result := ''
  else
    Result := 'The Yuantus CAD helper is still running and could not be stopped.' + #13#10 +
              'Close it (or confirm stopping it) and run setup again.';
end;

{ ----------------------------------------------------------------------- }
{ CAD-host startup STUB (taskbook §3.D; R1 narrows it -- see DEV MD §2). }
{ Append an idempotent, uniquely-fenced region to a per-user startup     }
{ file that NETLOADs the bridge DLL and loads the Lisp shell. The CAD    }
{ host loads it only once the cad-bridge folder is on its Support path   }
{ (operator one-time step in R1). Gated behind the 'cadconfig' task      }
{ (the --skip-cad-config opt-out).                                       }
{ ----------------------------------------------------------------------- }

function FenceBegin(): String;
begin
  Result := '{#FenceBegin}';
end;

function FenceEnd(): String;
begin
  Result := '{#FenceEnd}';
end;

function StartupBlock(): String;
var
  BridgeDll, LspFile: String;
begin
  BridgeDll := ExpandConstant('{userappdata}\YuantusPLM\cad-bridge\YuantusCadHelperBridge.dll');
  LspFile   := ExpandConstant('{userappdata}\YuantusPLM\cad-bridge\yuantus_cad_helper.lsp');
  { Lisp uses forward slashes happily; avoid backslash-escaping headaches. }
  StringChangeEx(BridgeDll, '\', '/', True);
  StringChangeEx(LspFile, '\', '/', True);
  Result :=
    FenceBegin() + #13#10 +
    '(if (findfile "' + BridgeDll + '") (command "_NETLOAD" "' + BridgeDll + '"))' + #13#10 +
    '(if (findfile "' + LspFile + '") (load "' + LspFile + '"))' + #13#10 +
    FenceEnd() + #13#10;
end;

{ Remove a previously-written fenced region from Content (idempotency +
  uninstall). Returns Content unchanged when no region is present. }
function StripFencedRegion(const Content: String): String;
var
  B, E, EAfter: Integer;
begin
  Result := Content;
  B := Pos(FenceBegin(), Result);
  if B = 0 then exit;
  E := Pos(FenceEnd(), Result);
  if (E = 0) or (E < B) then exit;
  EAfter := E + Length(FenceEnd());
  { swallow a trailing CRLF after the end marker if present }
  if Copy(Result, EAfter, 2) = #13#10 then EAfter := EAfter + 2;
  Result := Copy(Result, 1, B - 1) + Copy(Result, EAfter, Length(Result));
end;

{ Append (idempotently) the fenced block to a per-user CAD startup file. }
procedure WriteStartupConfig(const StartupFile: String);
var
  Content: String;
begin
  Content := '';
  if FileExists(StartupFile) then
    LoadStringFromFile(StartupFile, Content);
  { idempotent: strip any prior region first, then append the current one }
  Content := StripFencedRegion(Content);
  if (Content <> '') and (Copy(Content, Length(Content) - 1, 2) <> #13#10) then
    Content := Content + #13#10;
  Content := Content + StartupBlock();
  SaveStringToFile(StartupFile, Content, False);
end;

{ Per-user-writable AutoCAD/ZWCAD/GstarCAD startup file candidates. We
  target the user roaming support path (per-user-writable, no admin). If a
  host exposes none, the operator falls back to --skip-cad-config and the
  DEV/Verification MD documents it as manual-config-required. }
procedure ConfigureCadHostsStartup();
var
  UserStartup: String;
begin
  { A neutral per-user search-path acad.lsp under the Yuantus dir; the
    operator points each CAD host's trusted/support path at this file (the
    install runbook documents this). This keeps the write per-user and
    avoids touching machine-scoped support paths. }
  UserStartup := ExpandConstant('{userappdata}\YuantusPLM\cad-bridge\acad.lsp');
  WriteStartupConfig(UserStartup);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('cadconfig') then
      ConfigureCadHostsStartup();
  end;
end;

{ ----------------------------------------------------------------------- }
{ Uninstall: remove the fenced startup region (only that region), and     }
{ offer an explicit full-purge of the preserved user-data set. The        }
{ [UninstallDelete] section already removes the installer-laid binaries.  }
{ ----------------------------------------------------------------------- }

procedure RemoveStartupConfig();
var
  UserStartup, Content: String;
begin
  UserStartup := ExpandConstant('{userappdata}\YuantusPLM\cad-bridge\acad.lsp');
  if FileExists(UserStartup) then
  begin
    if LoadStringFromFile(UserStartup, Content) then
    begin
      Content := StripFencedRegion(Content);
      if Trim(Content) = '' then
        DeleteFile(UserStartup)
      else
        SaveStringToFile(UserStartup, Content, False);
    end;
  end;
end;

procedure FullPurgeUserData();
begin
  { Only reached inside the explicit opt-in branch below. These are the
    root-level user-data files preserved by default. The PLM bearer token
    is removed FIRST -- leaving it behind would let a reinstall stay
    logged in (HelperRuntime.cs:2912-2913 writes plm-bearer-token.bin +
    config.json at the root, alongside the S3/S6 files). }
  DeleteFile(ExpandConstant('{userappdata}\YuantusPLM\plm-bearer-token.bin'));
  DeleteFile(ExpandConstant('{userappdata}\YuantusPLM\config.json'));
  DeleteFile(ExpandConstant('{userappdata}\YuantusPLM\local-helper-token.dat'));
  DeleteFile(ExpandConstant('{userappdata}\YuantusPLM\audit.db'));
  DeleteFile(ExpandConstant('{userappdata}\YuantusPLM\install-id.json'));
  { Note: helper-session-*.json is intentionally NOT removed -- S3 owns it. }
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { best-effort stop so binary removal is not blocked by a file lock }
    TryStopRunningHelper();
    RemoveStartupConfig();
    { Default: PRESERVE the user-data set. Full purge is an explicit opt-in. }
    if MsgBox('Also remove your Yuantus data (local token, audit history)?' + #13#10 +
              'Choose No to keep it for a future reinstall.',
              mbConfirmation, MB_YESNO) = IDYES then
      FullPurgeUserData();
  end;
end;
