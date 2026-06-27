$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*paper_search_mcp.server*' -and
    $_.CommandLine -notlike '*powershell*' -and
    $_.CommandLine -notlike '*bash*'
}
if ($procs) {
    $procs | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Killed PID: $($_.ProcessId)"
    }
} else {
    Write-Host "No paper_search_mcp server processes found"
}
