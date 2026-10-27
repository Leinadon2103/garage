# Windows notification (toast) for Garage. Input: base64 of UTF-8 JSON {title, text}, so quotes and special
# characters in the text are no problem. Clicking the notification opens Garage (protocol garage:).
param([Parameter(Mandatory)][string]$Data, [string]$AppId = "Garage.Voertuigonderhoud")
$ErrorActionPreference = "Stop"
$d = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Data)) | ConvertFrom-Json
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$esc = { param($t) [Security.SecurityElement]::Escape([string]$t) }
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
<toast activationType="protocol" launch="garage:open">
  <visual><binding template="ToastGeneric">
    <text>$(& $esc $d.title)</text>
    <text>$(& $esc $d.text)</text>
  </binding></visual>
  <actions><action content="Garage openen" activationType="protocol" arguments="garage:open"/></actions>
</toast>
"@)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
