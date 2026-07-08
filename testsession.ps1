$headers = @{
    "App-Token"    = "your-app-token"
    "Content-Type" = "application/json"
}
$body = '{"user_token": "your-user-token"}'
try {
    Invoke-RestMethod -Uri "http://localhost/glpi/apirest.php/initSession" -Method Post -Headers $headers -Body $body
}
catch {
    $_.Exception.Response | Select-Object StatusCode, StatusDescription
    $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
    $reader.ReadToEnd()
}