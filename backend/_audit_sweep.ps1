$tok = Get-Content "$env:TEMP\_audit_tok.txt" -Raw
$H = @{ Authorization = "Bearer $tok" }
$spec = Invoke-RestMethod -Uri "http://127.0.0.1:8080/openapi.json" -Headers $H
$paths = $spec.paths.PSObject.Properties
$crashes = @()
$okCount = 0
$total = 0
foreach ($p in $paths) {
    $path = $p.Name
    $methods = $p.Value.PSObject.Properties.Name
    if ($methods -notcontains 'get') { continue }
    $total++
    $url = $path -replace '\{[^}]+\}', '1'
    $full = "http://127.0.0.1:8080$url"
    try {
        $resp = Invoke-WebRequest -Uri $full -Headers $H -Method GET -UseBasicParsing -TimeoutSec 8
        $code = [int]$resp.StatusCode
    } catch {
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode } else { $code = -1 }
    }
    if ($code -ge 500) { $crashes += "$code  $path" }
    elseif ($code -eq -1) { $crashes += "TIMEOUT/ERR  $path" }
    else { $okCount++ }
}
"TOTAL_GET_PATHS=$total" | Out-File "$env:TEMP\_audit_result.txt" -Encoding ascii
"OK=$okCount" | Out-File "$env:TEMP\_audit_result.txt" -Encoding ascii -Append
"CRASHES=$($crashes.Count)" | Out-File "$env:TEMP\_audit_result.txt" -Encoding ascii -Append
$crashes | Out-File "$env:TEMP\_audit_result.txt" -Encoding ascii -Append
"DONE" | Out-File "$env:TEMP\_audit_result.txt" -Encoding ascii -Append
