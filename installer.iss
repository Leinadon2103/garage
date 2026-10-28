; Installer for Garage (Inno Setup 6). Build: build.bat
; Installs per user (no admin rights needed) and registers itself under Settings > Apps.
; Texts the user sees are Dutch.

#define Version "1.4.0"

[Setup]
AppId={{8E3B6C52-4D0B-4A8E-9C1F-6A2D7E5B9F10}
AppName=Garage
AppVersion={#Version}
AppVerName=Garage {#Version}
AppPublisher=Daniel Goedknegt
DefaultDirName={autopf}\Garage
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=build\installer
OutputBaseFilename=Garage-installatie-{#Version}
SetupIconFile=app\static\garage.ico
UninstallDisplayIcon={app}\Garage.exe
UninstallDisplayName=Garage
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
; while Garage is open, (un)installing asks to close it first
AppMutex=Garage-Voertuigonderhoud
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "nl"; MessagesFile: "compiler:Languages\Dutch.isl"

[Tasks]
Name: "desktop"; Description: "Snelkoppeling op het bureaublad"; GroupDescription: "Snelkoppelingen:"
Name: "reminders"; Description: "Bij het aanmelden in Windows controleren of er onderhoud nodig is (melding)"; GroupDescription: "Herinneringen:"

[Files]
Source: "build\dist\Garage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; clean up old program files of a previous version (the data is in Documents\Garage)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
; AppUserModelID: needed to show Windows notifications from Garage (equal to APP_ID in Garage.pyw)
Name: "{autoprograms}\Garage"; Filename: "{app}\Garage.exe"; Comment: "Voertuigonderhoud bijhouden"; AppUserModelID: "Garage.Voertuigonderhoud"
Name: "{autodesktop}\Garage"; Filename: "{app}\Garage.exe"; Tasks: desktop

[Registry]
; reminders: short check (without a window) when signing in. The value name stays as in 1.3, so an upgrade
; replaces the old entry (with --controle) instead of adding a second one.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Garage herinneringen"; ValueData: """{app}\Garage.exe"" --check"; Flags: uninsdeletevalue; Tasks: reminders
; clicking a notification opens Garage (protocol garage:)
Root: HKCU; Subkey: "Software\Classes\garage"; ValueType: string; ValueData: "URL:Garage"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\garage"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\garage\DefaultIcon"; ValueType: string; ValueData: "{app}\Garage.exe,0"
Root: HKCU; Subkey: "Software\Classes\garage\shell\open\command"; ValueType: string; ValueData: """{app}\Garage.exe"""

[Run]
Filename: "{app}\Garage.exe"; Description: "Garage nu starten"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  WEBVIEW2 = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Present: Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not WebView2Present then
    Result := SuppressibleMsgBox(
      'Garage heeft de "Microsoft Edge WebView2 Runtime" nodig. Die lijkt niet geïnstalleerd te zijn.' + #13#13 +
      'Je kunt hem gratis downloaden via microsoft.com/edge/webview2.' + #13#13 + 'Toch doorgaan met installeren?',
      mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userdocs}\Garage');
    if DirExists(DataDir) and (SuppressibleMsgBox(
        'Wil je ook je gegevens verwijderen?' + #13#13 +
        'Dit zijn je voertuigen, onderhoud en facturen in:' + #13 + DataDir + #13#13 +
        'Kies "Nee" om ze te bewaren (bijvoorbeeld als je Garage later opnieuw installeert).',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
      DelTree(DataDir, True, True, True);
  end;
end;
