# 注册/更新 Windows 计划任务「DataLake Pull Outbox」：每 30 分钟从 VPS outbox 拉取一次 parquet。
#
# 用法（管理员或当前登录用户权限即可，因为任务用 InteractiveToken 跑）：
#   powershell -File .\register_pull_task.ps1
#
# 幂等：重复运行会用 /F 覆盖同名任务，方便以后改了 pull_outbox.sh 路径/调度再执行一次即可。

$TaskName = "\DataLake Pull Outbox"
$ScriptPath = "/mnt/e/Code/data-download/data-lake/pull_outbox.sh"
$Command = "wsl.exe -d Ubuntu -u root -- bash $ScriptPath"

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Pull parquet from VPS outbox every 30 min (robust: flock+retry+log). Script is git-tracked at data-download/data-lake/pull_outbox.sh.</Description>
    <URI>$TaskName</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <Duration>PT10M</Duration>
      <WaitTimeout>PT1H</WaitTimeout>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-07-13T00:00:00+08:00</StartBoundary>
      <Repetition>
        <Interval>PT30M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>wsl.exe</Command>
      <Arguments>-d Ubuntu -u root -- bash $ScriptPath</Arguments>
    </Exec>
  </Actions>
</Task>
"@

$tmpXml = Join-Path $env:TEMP "datalake-pull-task.xml"
[System.IO.File]::WriteAllText($tmpXml, $xml, [System.Text.Encoding]::Unicode)

schtasks /create /tn $TaskName /xml $tmpXml /f

Remove-Item $tmpXml -ErrorAction SilentlyContinue

Write-Host "已注册/更新任务: $TaskName -> $Command"
