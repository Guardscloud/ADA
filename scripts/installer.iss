; ADA kurulum programı (Inno Setup 6). Çıktı: dist\ADA-Setup.exe
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
OutputBaseFilename=ADA-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\ADA.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto
CloseApplications=no
#ifdef Sign
; build_installer.ps1 "ps" imza aracını tanımlar (scripts\sign.ps1): kurulum programı ve kaldırıcı imzalanır
SignTool=ps
SignedUninstaller=yes
#endif

[Languages]
; Kurulum, Windows'un diline göre açılır (desteklenmeyen dilde İngilizce); kullanıcı ilk ekranda değiştirebilir
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "nl"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[CustomMessages]
; Dil öneki olmayan satır tüm diller için varsayılandır (İngilizce); çevirisi olan diller üzerine yazar
DesktopIcon=Create a desktop shortcut
Shortcuts=Shortcuts:
LaunchNow=Launch ADA now
RemoveData=Also delete your ADA data?%n%nSettings, API keys, task history, routines and outputs:%n%1%n%nIf you choose "No", your data stays; reinstalling ADA picks up where you left off.
tr.DesktopIcon=Masaüstüne kısayol oluştur
tr.Shortcuts=Kısayollar:
tr.LaunchNow=ADA'yı şimdi başlat
tr.RemoveData=ADA verileriniz de silinsin mi?%n%nAyarlar, API anahtarları, görev geçmişi, rutinler ve çıktılar:%n%1%n%n"Hayır" derseniz veriler kalır; ADA'yı yeniden kurduğunuzda kaldığınız yerden devam edersiniz.
de.DesktopIcon=Desktop-Verknüpfung erstellen
de.Shortcuts=Verknüpfungen:
de.LaunchNow=ADA jetzt starten
de.RemoveData=Sollen Ihre ADA-Daten ebenfalls gelöscht werden?%n%nEinstellungen, API-Schlüssel, Aufgabenverlauf, Routinen und Ausgaben:%n%1%n%nBei "Nein" bleiben die Daten erhalten.
fr.DesktopIcon=Créer un raccourci sur le bureau
fr.Shortcuts=Raccourcis :
fr.LaunchNow=Lancer ADA maintenant
fr.RemoveData=Supprimer aussi vos données ADA ?%n%nParamètres, clés API, historique des tâches, routines et sorties :%n%1%n%nSi vous choisissez « Non », vos données sont conservées.
es.DesktopIcon=Crear un acceso directo en el escritorio
es.Shortcuts=Accesos directos:
es.LaunchNow=Iniciar ADA ahora
es.RemoveData=¿Eliminar también sus datos de ADA?%n%nAjustes, claves API, historial de tareas, rutinas y resultados:%n%1%n%nSi elige "No", sus datos se conservan.
pt.DesktopIcon=Criar atalho na área de trabalho
pt.Shortcuts=Atalhos:
pt.LaunchNow=Iniciar o ADA agora
pt.RemoveData=Excluir também seus dados do ADA?%n%nConfigurações, chaves de API, histórico de tarefas, rotinas e resultados:%n%1%n%nSe escolher "Não", seus dados serão mantidos.
it.DesktopIcon=Crea un collegamento sul desktop
it.Shortcuts=Collegamenti:
it.LaunchNow=Avvia ADA ora
it.RemoveData=Eliminare anche i dati di ADA?%n%nImpostazioni, chiavi API, cronologia delle attività, routine e risultati:%n%1%n%nScegliendo "No" i dati vengono conservati.
ru.DesktopIcon=Создать ярлык на рабочем столе
ru.Shortcuts=Ярлыки:
ru.LaunchNow=Запустить ADA сейчас
ru.RemoveData=Удалить также данные ADA?%n%nНастройки, API-ключи, история задач, рутины и результаты:%n%1%n%nЕсли выбрать «Нет», данные сохранятся.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:Shortcuts}"

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
Filename: "{app}\ADA.exe"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent
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
      if SuppressibleMsgBox(FmtMessage(CustomMessage('RemoveData'), [DataDir]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
