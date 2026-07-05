#Requires -RunAsAdministrator
<#
.SYNOPSIS
    cron-win.ps1 - Synchronize Windows Task Scheduler tasks from crontab.txt files.
.DESCRIPTION
    Reads C:\crontab.txt as the root registry file. Each file may contain:
      - Cron entries (5 fields + command) to register as scheduled tasks under "CronWin" folder.
      - Path: <dir> directives to recurse into another directory's crontab.txt.
    Tasks are synchronized: added, updated, or removed to match the current crontab state.
.NOTES
    Log file: C:\Temp\cron-win.log  (rotation: 7 days, max 5 rotated files kept)
#>

param(
    # Force re-registration of all tasks, ignoring change detection
    [switch]$Force
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Off

# --- Configuration ---

$LogFile        = "C:\Temp\cron-win.log"
$LogRotationDays = 7
$MaxLogFiles    = 5
$TaskFolder     = "CronWin"
$RootCrontab    = "C:\crontab.txt"

# --- Logging ---

function Initialize-Logging {
    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    # Rotate current log if it is older than $LogRotationDays days
    if (Test-Path $LogFile) {
        $age = (Get-Date) - (Get-Item $LogFile).LastWriteTime
        if ($age.Days -ge $LogRotationDays) {
            $ts   = (Get-Item $LogFile).LastWriteTime.ToString("yyyyMMdd_HHmmss")
            $base = [System.IO.Path]::GetFileNameWithoutExtension($LogFile)
            $ext  = [System.IO.Path]::GetExtension($LogFile)
            $dir  = Split-Path $LogFile -Parent
            Rename-Item $LogFile (Join-Path $dir "${base}_${ts}${ext}") -ErrorAction SilentlyContinue
        }
    }

    # Remove oldest rotated logs, keep only $MaxLogFiles
    $base = [System.IO.Path]::GetFileNameWithoutExtension($LogFile)
    $ext  = [System.IO.Path]::GetExtension($LogFile)
    $dir  = Split-Path $LogFile -Parent
    $rotated = Get-ChildItem -Path $dir -Filter "${base}_*${ext}" -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending
    if ($rotated -and $rotated.Count -gt $MaxLogFiles) {
        $rotated | Select-Object -Skip $MaxLogFiles | Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")][string]$Level = "INFO"
    )
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    } catch {
        Write-Host "[LOGGING ERROR] Could not write to log: $_"
    }
}

# --- Cron to Task Scheduler trigger conversion ---

function Build-ScheduledTrigger {
    param(
        [string]$Minute,
        [string]$Hour,
        [string]$DayOfMonth,
        [string]$Month,
        [string]$DayOfWeek
    )

    try {
        $allMin = ($Minute -eq "*")
        $allHr  = ($Hour -eq "*")
        $allDom = ($DayOfMonth -eq "*")
        $allMon = ($Month -eq "*")
        $allDow = ($DayOfWeek -eq "*")

        # Every minute: * * * * *
        if ($allMin -and $allHr -and $allDom -and $allMon -and $allDow) {
            return New-ScheduledTaskTrigger -Once `
                -At (Get-Date -Hour 0 -Minute 0 -Second 0) `
                -RepetitionInterval (New-TimeSpan -Minutes 1)
        }

        # Every N minutes: */N * * * *
        if ($Minute -match '^\*/(\d+)$' -and $allHr -and $allDom -and $allMon -and $allDow) {
            $n = [int]$Matches[1]
            return New-ScheduledTaskTrigger -Once `
                -At (Get-Date -Hour 0 -Minute 0 -Second 0) `
                -RepetitionInterval (New-TimeSpan -Minutes $n)
        }

        # Every N hours at fixed minute: M */N * * *
        if ($Minute -match '^\d+$' -and $Hour -match '^\*/(\d+)$' -and $allDom -and $allMon -and $allDow) {
            $m  = [int]$Minute
            $hn = [int]$Matches[1]
            return New-ScheduledTaskTrigger -Once `
                -At (Get-Date -Hour 0 -Minute $m -Second 0) `
                -RepetitionInterval (New-TimeSpan -Hours $hn)
        }

        $dayNames = @{
            "0" = "Sunday";  "7" = "Sunday"
            "1" = "Monday";  "2" = "Tuesday"; "3" = "Wednesday"
            "4" = "Thursday"; "5" = "Friday"; "6" = "Saturday"
            "sun" = "Sunday"; "mon" = "Monday"; "tue" = "Tuesday"
            "wed" = "Wednesday"; "thu" = "Thursday"; "fri" = "Friday"; "sat" = "Saturday"
        }

        # Weekly at specific time: M H * * DOW
        if ($Minute -match '^\d+$' -and $Hour -match '^\d+$' -and $allDom -and $allMon -and -not $allDow) {
            $timeStr = "{0:D2}:{1:D2}" -f ([int]$Hour), ([int]$Minute)
            $dows = @()
            foreach ($d in ($DayOfWeek -split ',')) {
                $key = $d.Trim().ToLower()
                if ($dayNames.ContainsKey($key)) { $dows += $dayNames[$key] }
            }
            if ($dows.Count -gt 0) {
                return New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dows -At $timeStr
            }
        }

        # Daily at specific time: M H * * *
        if ($Minute -match '^\d+$' -and $Hour -match '^\d+$' -and $allDom -and $allMon -and $allDow) {
            $timeStr = "{0:D2}:{1:D2}" -f ([int]$Hour), ([int]$Minute)
            return New-ScheduledTaskTrigger -Daily -At $timeStr
        }

        # Unsupported pattern: fall back to every-minute trigger
        Write-Log "Cron '$Minute $Hour $DayOfMonth $Month $DayOfWeek' not fully supported; using every-minute fallback" "WARN"
        return New-ScheduledTaskTrigger -Once `
            -At (Get-Date -Hour 0 -Minute 0 -Second 0) `
            -RepetitionInterval (New-TimeSpan -Minutes 1)

    } catch {
        Write-Log "Error building trigger for '$Minute $Hour $DayOfMonth $Month $DayOfWeek': $_" "ERROR"
        return $null
    }
}

# --- Action builder ---

function Build-ScheduledAction {
    param(
        [string]$Command,
        [string]$WorkingDir
    )

    # Detect if the command begins with a full path (e.g. C:\path\to\exe.exe)
    $firstToken = ""
    if ($Command -match '^"([^"]+)"') {
        $firstToken = $Matches[1]
    } elseif ($Command -match '^(\S+)') {
        $firstToken = $Matches[1]
    }

    $isFullPath = $firstToken -match '^[A-Za-z]:\\'

    if ($isFullPath) {
        if ($Command -match '^"([^"]+)"\s*(.*)$') {
            $exe  = $Matches[1]
            $args = $Matches[2].Trim()
        } elseif ($Command -match '^(\S+)\s*(.*)$') {
            $exe  = $Matches[1]
            $args = $Matches[2].Trim()
        } else {
            $exe  = $Command
            $args = ""
        }
        # Quote exe path if it contains spaces
        $exeArg = if ($exe -match '\s') { "`"$exe`"" } else { $exe }
        return New-ScheduledTaskAction -Execute $exeArg -Argument $args -WorkingDirectory $WorkingDir
    } else {
        # Use full path to cmd.exe so there is no ambiguity
        $cmdExe = "$env:SystemRoot\System32\cmd.exe"
        # Wrap the entire command in quotes for cmd /C "..."; escape any internal quotes as ""
        $escapedCommand = $Command -replace '"', '""'
        $arguments = "/C `"$escapedCommand`""
        return New-ScheduledTaskAction -Execute $cmdExe -Argument $arguments -WorkingDirectory $WorkingDir
    }
}

# --- Script name extractor (used for task naming) ---

function Get-ScriptNameFromCommand {
    param([string]$Command)

    # powershell -File "script name.ps1"  or  powershell -File script.ps1
    if ($Command -match '-[Ff]ile\s+"([^"]+)"') {
        return [System.IO.Path]::GetFileName($Matches[1])
    }
    if ($Command -match '-[Ff]ile\s+(\S+)') {
        return [System.IO.Path]::GetFileName($Matches[1])
    }

    # Full path to an interpreter/exe as first token (e.g. python.exe, node.exe):
    # prefer the script argument that follows it, so multiple scripts run through
    # the same interpreter don't collide on the interpreter's own filename.
    $rest = $null
    if ($Command -match '^"([A-Za-z]:[^"]+)"\s*(.*)$') {
        $exeName = [System.IO.Path]::GetFileName($Matches[1])
        $rest    = $Matches[2]
    } elseif ($Command -match '^([A-Za-z]:\S+)\s*(.*)$') {
        $exeName = [System.IO.Path]::GetFileName($Matches[1])
        $rest    = $Matches[2]
    }

    if ($null -ne $rest) {
        if ($rest -match '"([^"]+\.(py|ps1|js|rb|sh|bat|cmd|pl))"') {
            return [System.IO.Path]::GetFileName($Matches[1])
        }
        if ($rest -match '(\S+\.(py|ps1|js|rb|sh|bat|cmd|pl))\b') {
            return [System.IO.Path]::GetFileName($Matches[1])
        }
        return $exeName
    }

    # Fallback: sanitize command string
    return ($Command -replace '[\\/:*?"<>|]', '_' -replace '\s+', '_')
}

# --- Task name builder ---

function Get-TaskName {
    param(
        [string]$DirectoryPath,
        [string]$ScriptName
    )
    # Replace path separators and drive colon with underscores
    $sanitizedPath   = $DirectoryPath -replace '[:\\]', '_' -replace '^_+', ''
    $sanitizedScript = $ScriptName    -replace '[\\/:*?"<>|]', '_'
    $name = "${sanitizedPath}_${sanitizedScript}"
    # Windows Task Scheduler task name limit is around 230 characters
    if ($name.Length -gt 230) { $name = $name.Substring(0, 230) }
    return $name
}

# --- crontab.txt recursive parser ---

$script:visitedPaths = @{}

function Parse-CrontabFile {
    param([string]$CrontabPath)

    $results = [System.Collections.Generic.List[object]]::new()

    if (-not (Test-Path $CrontabPath)) {
        Write-Log "crontab.txt not found: $CrontabPath" "WARN"
        return $results
    }

    # Guard against circular references
    $resolved = Resolve-Path $CrontabPath -ErrorAction SilentlyContinue
    $canonical = if ($resolved) { $resolved.Path } else { $CrontabPath }
    if ($script:visitedPaths.ContainsKey($canonical)) {
        Write-Log "Already processed (skipping to avoid loop): $canonical" "WARN"
        return $results
    }
    $script:visitedPaths[$canonical] = $true

    $workingDir = Split-Path $CrontabPath -Parent
    Write-Log "Parsing: $CrontabPath"

    try {
        $lines = Get-Content $CrontabPath -Encoding UTF8 -ErrorAction Stop
    } catch {
        Write-Log "Cannot read '$CrontabPath': $_" "ERROR"
        return $results
    }

    $lineNum = 0
    $cronFieldRx = '^(\*|\d+(-\d+)?)(\\/\d+)?(,(\*|\d+(-\d+)?)(\\/\d+)?)*$'

    foreach ($rawLine in $lines) {
        $lineNum++
        $line = $rawLine.Trim()

        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
            continue
        }

        # Path: directive - recurse into another directory
        if ($line -match '^Path:\s*(.+)$') {
            $subDir     = $Matches[1].Trim().Trim('"')
            $subCrontab = Join-Path $subDir "crontab.txt"
            Write-Log "Following Path directive: $subDir"
            $subResults = Parse-CrontabFile -CrontabPath $subCrontab
            foreach ($r in $subResults) { $results.Add($r) }
            continue
        }

        # Cron entry: FIELD1 FIELD2 FIELD3 FIELD4 FIELD5 <command>
        if ($line -match '^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$') {
            $min = $Matches[1]
            $hr  = $Matches[2]
            $dom = $Matches[3]
            $mon = $Matches[4]
            $dow = $Matches[5]
            $cmd = $Matches[6].Trim()

            # Validate cron fields
            $validCron = $true
            foreach ($field in @($min, $hr, $dom, $mon, $dow)) {
                if ($field -notmatch $cronFieldRx) {
                    Write-Log "Line $lineNum : invalid cron field '$field' in: $line" "ERROR"
                    $validCron = $false
                    break
                }
            }
            if (-not $validCron) { continue }

            $scriptName = Get-ScriptNameFromCommand -Command $cmd
            $taskName   = Get-TaskName -DirectoryPath $workingDir -ScriptName $scriptName

            # Description doubles as a change-detection fingerprint
            $description = "CronWin|$min|$hr|$dom|$mon|$dow|$cmd|$workingDir"

            $entry = [PSCustomObject]@{
                TaskName    = $taskName
                Minute      = $min
                Hour        = $hr
                DayOfMonth  = $dom
                Month       = $mon
                DayOfWeek   = $dow
                Command     = $cmd
                WorkingDir  = $workingDir
                CronExpr    = "$min $hr $dom $mon $dow"
                Description = $description
            }

            $results.Add($entry)
            Write-Log "  Task: [$taskName]  cron: $min $hr $dom $mon $dow  cmd: $cmd"

        } else {
            Write-Log "Line $lineNum : cannot parse line (not a cron entry or Path directive): $line" "ERROR"
        }
    }

    return $results
}

# --- Task Scheduler folder management ---

function Ensure-TaskSchedulerFolder {
    param([string]$FolderName)

    $existing = Get-ScheduledTask -TaskPath "\$FolderName\" -ErrorAction SilentlyContinue
    if ($null -ne $existing -or $?) {
        # Folder already exists if Get-ScheduledTask did not throw
        # A more reliable check via COM:
    }

    try {
        $svc = New-Object -ComObject "Schedule.Service"
        $svc.Connect()
        try {
            $svc.GetFolder("\$FolderName") | Out-Null
        } catch {
            $root = $svc.GetFolder("\")
            $root.CreateFolder($FolderName) | Out-Null
            Write-Log "Created Task Scheduler folder: \$FolderName"
        }
    } catch {
        Write-Log "Error ensuring task folder '$FolderName': $_" "ERROR"
        throw
    }
}

# --- Main synchronization ---

function Sync-Tasks {
    param(
        [object[]]$DesiredTasks,
        [switch]$Force
    )

    # Collect current tasks in CronWin folder
    $existingTasks = @{}
    $existing = Get-ScheduledTask -TaskPath "\$TaskFolder\" -ErrorAction SilentlyContinue
    if ($existing) {
        foreach ($t in $existing) {
            $existingTasks[$t.TaskName] = $t
        }
    }
    Write-Log "Existing tasks in \$TaskFolder\: $($existingTasks.Count)"
    if ($Force) { Write-Log "Force mode: all tasks will be re-registered" "WARN" }

    # Build desired map (last definition wins in case of duplicates)
    $desiredMap = @{}
    foreach ($t in $DesiredTasks) {
        if ($desiredMap.ContainsKey($t.TaskName)) {
            Write-Log "Duplicate task name '$($t.TaskName)' - last definition takes precedence" "WARN"
        }
        $desiredMap[$t.TaskName] = $t
    }
    Write-Log "Desired tasks from crontab: $($desiredMap.Count)"

    # Remove tasks that no longer exist in crontab
    foreach ($taskName in @($existingTasks.Keys)) {
        if (-not $desiredMap.ContainsKey($taskName)) {
            Write-Log "Removing obsolete task: $taskName"
            try {
                Unregister-ScheduledTask -TaskPath "\$TaskFolder\" -TaskName $taskName -Confirm:$false -ErrorAction Stop
                Write-Log "Removed: $taskName"
            } catch {
                Write-Log "Error removing task '$taskName': $_" "ERROR"
            }
        }
    }

    # Add new tasks and update changed ones
    foreach ($taskName in $desiredMap.Keys) {
        $desired       = $desiredMap[$taskName]
        $needsRegister = $false

        try {
            # Build action first so its resolved exe path is part of the fingerprint
            $action = Build-ScheduledAction -Command $desired.Command -WorkingDir $desired.WorkingDir

            # Fingerprint includes cron fields, raw command, working dir AND resolved exe+args
            $fingerprint = "CronWin|$($desired.Minute)|$($desired.Hour)|$($desired.DayOfMonth)|$($desired.Month)|$($desired.DayOfWeek)|$($desired.Command)|$($desired.WorkingDir)|exe=$($action.Execute)|args=$($action.Arguments)"

        if ($Force) {
            Write-Log "Force: re-registering task: $taskName"
            $needsRegister = $true
        } elseif ($existingTasks.ContainsKey($taskName)) {
            $existingDesc = $existingTasks[$taskName].Description
            if ($existingDesc -ne $fingerprint) {
                Write-Log "Updating changed task: $taskName"
                $needsRegister = $true
            } else {
                Write-Log "Task unchanged, skipping: $taskName"
            }
        } else {
            Write-Log "Registering new task: $taskName"
            $needsRegister = $true
        }

        if (-not $needsRegister) { continue }

        try {
            $trigger = Build-ScheduledTrigger `
                -Minute     $desired.Minute `
                -Hour       $desired.Hour `
                -DayOfMonth $desired.DayOfMonth `
                -Month      $desired.Month `
                -DayOfWeek  $desired.DayOfWeek

            if ($null -eq $trigger) {
                Write-Log "Skipping task (trigger could not be built): $taskName" "ERROR"
                continue
            }

            # action is already built above

            $settings = New-ScheduledTaskSettingsSet `
                -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
                -MultipleInstances  IgnoreNew `
                -StartWhenAvailable

            $currentUser = "$env:USERDOMAIN\$env:USERNAME"
            $principal = New-ScheduledTaskPrincipal `
                -UserId    $currentUser `
                -LogonType Interactive `
                -RunLevel  Highest

            Register-ScheduledTask `
                -TaskPath    "\$TaskFolder\" `
                -TaskName    $taskName `
                -Action      $action `
                -Trigger     $trigger `
                -Settings    $settings `
                -Principal   $principal `
                -Description $fingerprint `
                -Force       | Out-Null

            Write-Log "Registered: $taskName  ($($desired.CronExpr) | $($desired.Command))"

        } catch {
            Write-Log "Error registering task '$taskName': $_" "ERROR"
        }

        } catch {
            Write-Log "Error building action for task '$taskName': $_" "ERROR"
        }
    }
}

# --- Entry point ---

Initialize-Logging
Write-Log "=== cron-win.ps1 started ==="

Ensure-TaskSchedulerFolder -FolderName $TaskFolder

$script:visitedPaths = @{}
$allTasks = @(Parse-CrontabFile -CrontabPath $RootCrontab)

Write-Log "Total tasks parsed: $($allTasks.Count)"

Sync-Tasks -DesiredTasks $allTasks -Force:$Force

Write-Log "=== cron-win.ps1 completed ==="

