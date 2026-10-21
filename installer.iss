; Installatieprogramma voor Garage (Inno Setup 6). Bouwen: bouwen.bat
; Installeert per gebruiker (geen beheerdersrechten nodig) en registreert zich onder Instellingen > Apps.

#define Versie "1.2.0"

[Setup]
AppId={{8E3B6C52-4D0B-4A8E-9C1F-6A2D7E5B9F10}
AppName=Garage
AppVersion={#Versie}
AppVerName=Garage {#Versie}
AppPublisher=Daniel Goedknegt
DefaultDirName={autopf}\Garage
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=build\installer
OutputBaseFilename=Garage-installatie-{#Versie}
SetupIconFile=app\static\garage.ico
UninstallDisplayIcon={app}\Garage.exe
UninstallDisplayName=Garage
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; zolang Garage open is, vraagt (de)installeren om het eerst te sluiten
AppMutex=Garage-Voertuigonderhoud
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "nl"; MessagesFile: "compiler:Languages\Dutch.isl"

[Tasks]
Name: "bureaublad"; Description: "Snelkoppeling op het bureaublad"; GroupDescription: "Snelkoppelingen:"
Name: "herinneringen"; Description: "Bij het aanmelden in Windows controleren of er onderhoud nodig is (melding)"; GroupDescription: "Herinneringen:"

[Files]
Source: "build\dist\Garage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; oude programmabestanden van een vorige versie opruimen (gegevens staan in Documenten\Garage)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
; AppUserModelID: nodig om Windows-meldingen van Garage te kunnen tonen (gelijk aan APP_ID in Garage.pyw)
Name: "{autoprograms}\Garage"; Filename: "{app}\Garage.exe"; Comment: "Voertuigonderhoud bijhouden"; AppUserModelID: "Garage.Voertuigonderhoud"
Name: "{autodesktop}\Garage"; Filename: "{app}\Garage.exe"; Tasks: bureaublad

[Registry]
; herinneringen: korte controle (zonder venster) bij het aanmelden
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Garage herinneringen"; ValueData: """{app}\Garage.exe"" --controle"; Flags: uninsdeletevalue; Tasks: herinneringen
; klik op een melding opent Garage (protocol garage:)
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

function WebView2Aanwezig: Boolean;
var
  Versie: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2, 'pv', Versie) and (Versie <> '') and (Versie <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2, 'pv', Versie) and (Versie <> '') and (Versie <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2, 'pv', Versie) and (Versie <> '') and (Versie <> '0.0.0.0'));
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not WebView2Aanwezig then
    Result := SuppressibleMsgBox(
      'Garage heeft de "Microsoft Edge WebView2 Runtime" nodig. Die lijkt niet geïnstalleerd te zijn.' + #13#13 +
      'Je kunt hem gratis downloaden via microsoft.com/edge/webview2.' + #13#13 + 'Toch doorgaan met installeren?',
      mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Gegevens: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Gegevens := ExpandConstant('{userdocs}\Garage');
    if DirExists(Gegevens) and (SuppressibleMsgBox(
        'Wil je ook je gegevens verwijderen?' + #13#13 +
        'Dit zijn je voertuigen, onderhoud en facturen in:' + #13 + Gegevens + #13#13 +
        'Kies "Nee" om ze te bewaren (bijvoorbeeld als je Garage later opnieuw installeert).',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
      DelTree(Gegevens, True, True, True);
  end;
end;
