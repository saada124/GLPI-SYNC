$body = @{user_token='aZJJpf8A8FKjvO0hqikf150YS1Op9Np6owX6iECu'} | ConvertTo-Json
$h = @{'App-Token'='v3Hf4GN4xnk7JN9zXLfEFYbQ8ABnlXqnAxjEsWs4'; 'Content-Type'='application/json'}
$s = Invoke-RestMethod -Uri 'http://localhost/glpi/apirest.php/initSession' -Method Post -Headers $h -Body $body
$sh = @{'App-Token'='v3Hf4GN4xnk7JN9zXLfEFYbQ8ABnlXqnAxjEsWs4'; 'Session-Token'=$s.session_token; 'Content-Type'='application/json'}

$tests = @(
    @{name="A POST is_deleted only"; method="Post"; body='{"is_deleted":0}'},
    @{name="B POST full params"; method="Post"; body='{"start":0,"limit":9999,"is_deleted":0}'},
    @{name="C GET no params"; method="Get"; body=$null},
    @{name="D GET is_deleted param"; method="Get"; body=$null; urlExtra='?is_deleted=0'},
    @{name="E GET start&limit"; method="Get"; body=$null; urlExtra='?start=0&limit=9999&is_deleted=0'}
)

$types = @("Supplier", "ITILCategory", "ComputerType", "User")

foreach ($t in $types) {
    Write-Output "--- $t ---"
    foreach ($test in $tests) {
        $url = "http://localhost/glpi/apirest.php/search/$t$($test.urlExtra)"
        try {
            if ($test.body) {
                $r = Invoke-RestMethod -Uri $url -Method $test.method -Headers $sh -Body $test.body
            } else {
                $r = Invoke-RestMethod -Uri $url -Method $test.method -Headers $sh
            }
            Write-Output "  $($test.name): OK ($($r.data.count) items)"
            if ($r.data.count -gt 0) {
                $r.data | Select-Object id, name | ForEach-Object { Write-Output "    $($_.id): $($_.name)" }
            }
        } catch {
            Write-Output "  $($test.name): FAILED"
        }
    }
}
