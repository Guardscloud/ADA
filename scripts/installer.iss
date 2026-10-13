; ADA kurulum programı (Inno Setup 6). Çıktı: dist\ADA-Kurulum.exe
; Derleme: scripts\build_installer.ps1  (önce dist\ADA\ADA.exe üretilmiş olmalı)
;
; Yönetici izni istemez: uygulama %LOCALAPPDATA%\Programs\ADA altına, yalnızca bu kullanıcı için kurulur.
; Kullanıcı verisi (%APPDATA%\ADA: ayarlar, API anahtarları, görev geçmişi, rutinler) kurulumda ve
; güncellemede dokunulmaz; kaldırırken silinip silinmeyeceği sorulur. İlk açılış sorularını uygulamanın
; kendisi sorar (bkz. frontend/src/components/SetupWizard.jsx).

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
; Sınama kurulumları gerçek kurulumla çakışmasın diye komut satırından değiştirilebilir (/DAppId=… /DAppName=…)
#ifndef AppId
  #define AppId "6B0E2C5A-7D41-4E0B-9A6F-ADA0A61D2026"
#endif
#ifndef AppName
  #define AppName "ADA"
#endif

[Setup]
AppId={{{#AppId}}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
AppPublisher=Duck Team
DefaultDirName={localappdata}\Programs\ADA
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=ADA-Kurulum
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\ADA.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
#ifdef Sign
; build_installer.ps1 "ps" imza aracını tanımlar (scripts\sign.ps1): kurulum programı ve kaldırıcı imzalanır
SignTool=ps
SignedUninstaller=yes
#endif

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Kısayollar:"

[Files]
Source: "..\dist\ADA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Güncellemede önceki sürümün artık dosyaları kalmasın
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\ADA.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ADA.exe"; Tasks: desktopicon

[Registry]
; "Windows açılınca başlat" değerini uygulama yazar (ilk açılış soruları / Ayarlar); kaldırırken silinir
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "ADA"; Flags: dontcreatekey uninsdeletevalue

[Run]
Filename: "{app}\ADA.exe"; Description: "ADA'yı şimdi başlat"; Flags: nowait postinstall skipifsilent
; Uygulama içi güncelleme kurulumu sessiz çalıştırır (/RELAUNCH=1): bitince ADA yeniden açılır
Filename: "{app}\ADA.exe"; Flags: nowait; Check: RelaunchRequested

[Code]
function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

procedure StopAda;
var
  ResultCode: Integer;
  Cmd: String;
begin
  { Çalışan ADA dosyaları kilitler: kurulumdan/kaldırmadan önce kapatılır. Yalnızca BU kurulum klasöründen
    çalışan ADA.exe süreçleri kapatılır; başka klasördeki kopyalara ve süreç ağacına dokunulmaz (uygulama içi
    güncellemede kurulum programı ADA'nın alt süreci olarak başlar, ağaçla birlikte kendisi de kapanırdı) }
  Cmd := '-NoProfile -NonInteractive -Command "$d = $env:ADA_SETUP_DIR + [char]92; Get-Process ADA -ErrorAction SilentlyContinue | ' +
         'Where-Object { $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force"';
  SetEnvironmentVariable('ADA_SETUP_DIR', ExpandConstant('{app}'));
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1200);
end;

function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopAda;
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopAda;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\ADA');
    if DirExists(DataDir) then
      if SuppressibleMsgBox('ADA verileriniz de silinsin mi?' + #13#10 + #13#10 +
          'Ayarlar, API anahtarları, görev geçmişi, rutinler ve çıktılar:' + #13#10 + DataDir + #13#10 + #13#10 +
          '"Hayır" derseniz veriler kalır; ADA''yı yeniden kurduğunuzda kaldığınız yerden devam edersiniz.',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
