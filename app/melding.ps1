# Windows-melding (toast) voor Garage. Invoer: base64 van UTF-8 JSON {titel, tekst}, zodat aanhalingstekens
# en speciale tekens in de tekst geen probleem zijn. Klikken op de melding opent Garage (protocol garage:).
param([Parameter(Mandatory)][string]$Gegevens, [string]$AppId = "Garage.Voertuigonderhoud")
$ErrorActionPreference = "Stop"
$g = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Gegevens)) | ConvertFrom-Json
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$esc = { param($t) [Security.SecurityElement]::Escape([string]$t) }
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
<toast activationType="protocol" launch="garage:open">
  <visual><binding template="ToastGeneric">
    <text>$(& $esc $g.titel)</text>
    <text>$(& $esc $g.tekst)</text>
  </binding></visual>
  <actions><action content="Garage openen" activationType="protocol" arguments="garage:open"/></actions>
</toast>
"@)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
